"use client";
import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import {
  canCancel,
  costRows,
  restoreCommand,
  retainCommand,
} from "../lib/platform";

type Task = {
  task_id: string;
  state: string;
  task_version: number;
  result?: unknown;
  costs?: {
    confirmed_micro_usd: number;
    estimated_micro_usd?: number;
    uncertain_micro_usd: number;
  };
};
type Step = { step_id: string; path: string; state: string };
type System = { submission_available: boolean; execution_epoch: string };
const samples: Task[] = [
  {
    task_id: "sample-01",
    state: "succeeded",
    task_version: 4,
    result: {
      summary: "Revenue reached 42 units in the synthetic source.",
      citations: [
        { quote: "Revenue is 42.", source: "Synthetic dossier · example only" },
      ],
    },
    costs: {
      estimated_micro_usd: 0,
      confirmed_micro_usd: 2400,
      uncertain_micro_usd: 0,
    },
  },
  {
    task_id: "sample-02",
    state: "suspended",
    task_version: 3,
    costs: {
      estimated_micro_usd: 0,
      confirmed_micro_usd: 0,
      uncertain_micro_usd: 1000,
    },
  },
  {
    task_id: "sample-03",
    state: "failed",
    task_version: 2,
    costs: {
      estimated_micro_usd: 0,
      confirmed_micro_usd: 600,
      uncertain_micro_usd: 0,
    },
  },
];
const names = [
  "Quarterly revenue brief",
  "Unresolved provider response",
  "Rejected model output",
];
const sampleSteps: Step[] = [
  { step_id: "1", path: "model / analysis", state: "succeeded" },
  { step_id: "2", path: "tool / read_excerpt", state: "succeeded" },
  { step_id: "3", path: "model / structured report", state: "succeeded" },
  { step_id: "4", path: "orchestrator / save_report", state: "succeeded" },
];
class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
  }
}
async function api(
  path: string,
  init?: RequestInit,
): Promise<Record<string, unknown>> {
  const response = await fetch("/api/platform/" + path, {
    ...init,
    cache: "no-store",
    signal: AbortSignal.timeout(18000),
  });
  const data = await response.json();
  if (!response.ok)
    throw new ApiError(
      typeof data.error === "string"
        ? data.error
        : typeof data.detail === "string"
          ? data.detail
          : `API returned ${response.status}`,
      response.status,
    );
  return data;
}
export default function Workspace() {
  const [live, setLive] = useState(false);
  const [configured, setConfigured] = useState(false);
  const [tasks, setTasks] = useState<Task[]>(samples);
  const [selected, setSelected] = useState("sample-01");
  const [detail, setDetail] = useState<Task | null>(samples[0]);
  const [steps, setSteps] = useState<Step[]>(sampleSteps);
  const [system, setSystem] = useState<System | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [text, setText] = useState(
    "Analyze this synthetic dossier: Revenue is 42 units. All data is fictional.",
  );
  const [workflow, setWorkflow] = useState("");
  const [workflows, setWorkflows] = useState<
    { name: string; version: string }[]
  >([]);
  const [pending, setPending] = useState<{
    path: string;
    body: string;
    key: string;
    epoch: string;
  } | null>(null);
  const [tab, setTab] = useState("Overview");
  const serial = useRef(0);
  useEffect(() => {
    fetch("/api/config")
      .then((r) => r.json())
      .then((v) => {
        setConfigured(v.configured === true);
        try {
          const saved = restoreCommand(sessionStorage.getItem("atp-pending"));
          if (saved) {
            setPending(saved);
            setLive(true);
            setTasks([]);
            setDetail(null);
            setSelected("");
          }
        } catch {
          setError(
            "Session storage is unavailable. Writes will remain blocked if a command cannot be retained.",
          );
        }
      })
      .catch(() => setError("Could not read local configuration."));
  }, []);
  useEffect(() => {
    if (!live) return;
    let active = true;
    const generation = ++serial.current;
    let loading = false;
    async function refresh() {
      if (loading || document.hidden) return;
      loading = true;
      try {
        const [list, status, catalogue] = await Promise.all([
          api("tasks?limit=50"),
          api("system"),
          api("workflows"),
        ]);
        let task: Record<string, unknown> | null = null;
        let progress: Record<string, unknown> | null = null;
        if (selected) {
          [task, progress] = await Promise.all([
            api("tasks/" + selected),
            api("tasks/" + selected + "/steps?limit=50"),
          ]);
        }
        if (active && serial.current === generation) {
          setTasks(list.items as Task[]);
          setSystem(status as unknown as System);
          setWorkflows(
            (catalogue.items as { name: string; version: string }[]).filter(
              (w) => w.name === "analysis" && w.version === "1",
            ),
          );
          setDetail(task as Task | null);
          setSteps((progress?.items ?? []) as Step[]);
          setError("");
        }
      } catch (e) {
        if (active) {
          setError(e instanceof Error ? e.message : "Local read failed");
          setSystem(null);
          setDetail(null);
        }
      } finally {
        loading = false;
      }
    }
    void refresh();
    const timer = setInterval(() => void refresh(), 5000);
    return () => {
      active = false;
      clearInterval(timer);
    };
  }, [live, selected, busy]);
  function mode(value: boolean) {
    if (pending || busy) return;
    setLive(value);
    setSelected(value ? "" : "sample-01");
    setTasks(value ? [] : samples);
    setDetail(value ? null : samples[0]);
    setSteps(value ? [] : sampleSteps);
    setError("");
    setSystem(null);
    setPending(null);
  }
  function pick(task: Task) {
    setSelected(task.task_id);
    setDetail(live ? null : task);
    setSteps(
      live
        ? []
        : task.state === "succeeded"
          ? sampleSteps
          : [
              {
                step_id: "1",
                path: "model / analysis",
                state: task.state === "suspended" ? "unknown" : "failed",
              },
            ],
    );
  }
  async function send(command: NonNullable<typeof pending>) {
    try {
      sessionStorage.setItem("atp-pending", JSON.stringify(command));
    } catch {
      setError("Cannot retain command safely in this tab. No request sent.");
      return;
    }
    setBusy(true);
    setError("");
    setPending(command);
    try {
      const result = await api(command.path, {
        method: "POST",
        headers: {
          "content-type": "application/json",
          "idempotency-key": command.key,
          "x-execution-epoch": command.epoch,
        },
        body: command.body,
      });
      sessionStorage.removeItem("atp-pending");
      setPending(null);
      if (typeof result.task_id === "string") setSelected(result.task_id);
    } catch (e) {
      if (e instanceof ApiError && !retainCommand(e.status)) {
        sessionStorage.removeItem("atp-pending");
        setPending(null);
      }
      setError(e instanceof Error ? e.message : "Command outcome unknown.");
    } finally {
      setBusy(false);
    }
  }
  function create() {
    if (!system || !workflow || pending) return;
    const [name, version] = workflow.split(":");
    void send({
      path: "tasks",
      body: JSON.stringify({
        workflow: { name, version },
        input: { texts: [text] },
      }),
      key: crypto.randomUUID(),
      epoch: system.execution_epoch,
    });
  }
  function cancel() {
    if (!detail || !system || pending) return;
    void send({
      path: `tasks/${detail.task_id}/cancel`,
      body: JSON.stringify({ expected_task_version: detail.task_version }),
      key: crypto.randomUUID(),
      epoch: system.execution_epoch,
    });
  }
  return (
    <div className="workspace">
      <a className="skip" href="#content">
        Skip to workspace
      </a>
      <aside className="rail">
        <Link className="wordmark" href="/">
          taskroom<span> / ATP</span>
        </Link>
        <p className="label">EXECUTION WORKSPACE</p>
        <nav aria-label="Workspace">
          <a href="#content" className="current">
            01 <span>Tasks</span>
            <b>↗</b>
          </a>
          <a href="#create">
            02 <span>New analysis</span>
          </a>
          <a href="#boundaries">
            03 <span>About this demo</span>
          </a>
        </nav>
        <div className="rail-foot">
          <span className="dot" /> LOCAL FIRST
          <p>
            No subscriptions.
            <br />
            No paid model calls.
          </p>
          <a href="https://portfolio-wheat-five-66.vercel.app/projects/ai-task-execution-platform">
            Engineering case study ↗
          </a>
        </div>
      </aside>
      <main id="content">
        <header className="topbar">
          <span>AI TASK EXECUTION PLATFORM</span>
          <span className="local">127.0.0.1 / PRIVATE WORKSPACE</span>
        </header>
        <section className="hero">
          <div>
            <p className="label">01 / WORKSPACE</p>
            <h1>
              Work that
              <br />
              <em>holds its state.</em>
            </h1>
            <p className="deck">
              Follow a task from intent to evidence.
              <br />
              Keep progress, outcomes, and uncertainty in view.
            </p>
          </div>
          <div className="mode">
            <p className="label">DATA SOURCE</p>
            <div role="group" aria-label="Data source">
              <button
                aria-pressed={!live}
                onClick={() => mode(false)}
                disabled={busy || !!pending}
              >
                Sample preview
              </button>
              <button
                aria-pressed={live}
                onClick={() => mode(true)}
                disabled={!configured || busy || !!pending}
              >
                Local API
              </button>
            </div>
            <p>
              {live
                ? "Connected reads · refresh every 5 seconds."
                : "Illustrative data. No backend execution or provider calls."}
            </p>
            {!configured && (
              <p>
                Local API needs an owner key in <code>.env.local</code>. See
                frontend/README.md.
              </p>
            )}
          </div>
        </section>
        <div className="facts">
          <div>
            <strong>{live ? tasks.length : "03"}</strong>
            <span>{live ? "TASKS IN FIRST PAGE" : "EXAMPLE SCENARIOS"}</span>
          </div>
          <div>
            <strong>
              {live ? (system ? "Online" : "Checking") : "Sample"}
            </strong>
            <span>DATA SOURCE STATUS</span>
          </div>
          <div>
            <strong>
              {live ? (system?.submission_available ? "Open" : "Closed") : "$0"}
            </strong>
            <span>{live ? "TASK ADMISSION" : "ACTUAL PROVIDER SPEND"}</span>
          </div>
        </div>
        {error && (
          <div className="notice error" role="alert">
            <strong>Request needs attention</strong>
            <p>{error}</p>
          </div>
        )}
        {pending && (
          <div className="notice">
            <p>
              A command is retained in this tab’s session storage (including its
              input text). It survives reload; keep this tab until the outcome
              is resolved. Its outcome may be unknown. Retry sends the same
              payload and idempotency key. Do not create another command to
              replace it.
            </p>
            <button disabled={busy} onClick={() => void send(pending)}>
              Retry same command
            </button>
          </div>
        )}
        <section className="task-section">
          <div className="section-head">
            <h2>Execution ledger</h2>
            <span className="label">
              {live
                ? "OWNER-SCOPED / FIRST 50 TASKS"
                : "SAMPLE DATA / NOT LIVE"}
            </span>
          </div>
          <div className="task-grid">
            <div className="task-list" aria-label="Tasks">
              {tasks.length === 0 ? (
                <p className="empty">
                  {live
                    ? "No tasks loaded. Configure access or create your first analysis."
                    : "No samples."}
                </p>
              ) : (
                tasks.map((task, i) => (
                  <button
                    key={task.task_id}
                    className="task-row"
                    aria-pressed={selected === task.task_id}
                    onClick={() => pick(task)}
                  >
                    <span className="task-index">
                      {String(i + 1).padStart(2, "0")}
                    </span>
                    <span>
                      <strong>{live ? "Analysis task" : names[i]}</strong>
                      <small>{task.task_id}</small>
                    </span>
                    <span className={"badge " + task.state}>{task.state}</span>
                  </button>
                ))
              )}
            </div>
            <article className="inspector" aria-label="Selected task">
              <p className="label">TASK INSPECTOR</p>
              {!detail ? (
                <p className="empty">
                  Select a task to inspect its durable state.
                </p>
              ) : (
                <>
                  <div className="detail-head">
                    <h3>
                      {live
                        ? "Analysis"
                        : names[tasks.findIndex((t) => t.task_id === selected)]}
                    </h3>
                    <span className={"badge " + detail.state}>
                      {detail.state}
                    </span>
                  </div>
                  <p className="mono">
                    Version {detail.task_version} · {detail.task_id}
                  </p>
                  <div className="tabs" role="group" aria-label="Task view">
                    {["Overview", "Steps", "Result"].map((t) => (
                      <button
                        key={t}
                        aria-pressed={tab === t}
                        onClick={() => setTab(t)}
                      >
                        {t}
                      </button>
                    ))}
                  </div>
                  {tab === "Overview" && (
                    <>
                      <p className="state-copy">
                        {detail.state === "suspended"
                          ? "Execution is suspended. An unknown provider outcome must not trigger automatic regeneration."
                          : detail.state === "succeeded"
                            ? "The task has a committed successful outcome."
                            : detail.state === "failed"
                              ? "Execution was rejected. Known billable usage is still accounted for."
                              : "State is read from the selected task. Queued work requires a separately configured compatible worker."}
                      </p>
                      <div className="costs">
                        {costRows(detail.costs).map(([label, value]) => (
                          <div key={label}>
                            <span>{label}</span>
                            <strong>{value}</strong>
                          </div>
                        ))}
                      </div>
                      {!live && (
                        <p className="muted">
                          Illustrative accounting figures only. Actual spend:
                          $0.
                        </p>
                      )}
                    </>
                  )}
                  {tab === "Steps" && (
                    <ol className="steps">
                      {steps.map((s) => (
                        <li key={s.step_id}>
                          <span>{s.path}</span>
                          <span className={"badge " + s.state}>{s.state}</span>
                        </li>
                      ))}
                    </ol>
                  )}
                  {tab === "Result" &&
                    (detail.result ? (
                      <pre>{JSON.stringify(detail.result, null, 2)}</pre>
                    ) : (
                      <p className="empty">
                        No report is available for this task. No result is
                        generated by this interface.
                      </p>
                    ))}
                  {live && canCancel(detail.state) && (
                    <button
                      className="cancel"
                      disabled={busy || !!pending || !system}
                      onClick={cancel}
                    >
                      Cancel task
                    </button>
                  )}
                </>
              )}
            </article>
          </div>
        </section>
        <section id="create" className="create-section">
          <div>
            <p className="label">02 / NEW ANALYSIS</p>
            <h2>
              A clear input.
              <br />
              <em>An explicit boundary.</em>
            </h2>
            <p>
              Use synthetic text. The interface submits to the existing API; it
              does not start workers or enable dispatch.
            </p>
          </div>
          <form
            onSubmit={(e) => {
              e.preventDefault();
              create();
            }}
          >
            <label htmlFor="workflow">Workflow</label>
            <select
              id="workflow"
              value={workflow}
              onChange={(e) => setWorkflow(e.target.value)}
              disabled={!live || busy || !!pending}
            >
              <option value="">
                {live
                  ? "Select an available workflow"
                  : "Sample preview · switch to Local API to submit"}
              </option>
              {workflows.map((w) => (
                <option
                  key={w.name + ":" + w.version}
                  value={w.name + ":" + w.version}
                >
                  {w.name} / v{w.version}
                </option>
              ))}
            </select>
            <label htmlFor="source">Source text</label>
            <textarea
              id="source"
              maxLength={8000}
              rows={5}
              value={text}
              onChange={(e) => setText(e.target.value)}
              disabled={busy || !!pending}
            />
            <div className="form-foot">
              <span>{text.length} / 8,000 characters</span>
              <button
                className="primary"
                disabled={
                  !live ||
                  !system?.submission_available ||
                  !workflow ||
                  !text.trim() ||
                  busy ||
                  !!pending
                }
              >
                Create task ↗
              </button>
            </div>
            {live && !system?.submission_available && (
              <p>
                Admission is closed or unavailable. This interface cannot enable
                it.
              </p>
            )}
          </form>
        </section>
        <footer id="boundaries">
          <span className="label">03 / KNOW THE BOUNDARY</span>
          <p>
            Free local software. Sample results are illustrative; Local API uses
            your existing owner permissions. No OpenAI key, admin operations,
            automatic regeneration, or public hosting. Polling is used here; the
            backend also supports SSE.
          </p>
          <span>BUILT FOR DURABILITY, NOT ILLUSION.</span>
        </footer>
      </main>
    </div>
  );
}
