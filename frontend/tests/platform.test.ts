import { test } from "node:test";
import assert from "node:assert/strict";
import { canCancel, money, gateway } from "../lib/platform.ts";
const base = "http://localhost:3000/api/platform/";
const uuid = "00000000-0000-4000-8000-000000000001";
const request = (
  path: string,
  method = "GET",
  origin?: string,
  body?: string,
) =>
  new Request(base + path, {
    method,
    headers: {
      ...(origin ? { origin } : {}),
      "content-type": "application/json",
      "idempotency-key": "stable-command-key",
      "x-execution-epoch": uuid,
    },
    body,
  });
test("terminal tasks cannot be cancelled; suspended tasks can", () => {
  for (const s of ["queued", "running", "suspended"])
    assert.equal(canCancel(s), true);
  for (const s of ["succeeded", "failed", "cancelled", "timed_out", "other"])
    assert.equal(canCancel(s), false);
});
test("money preserves integer micro USD and distinguishes missing usage", () => {
  assert.equal(money(1234567), "$1.234567");
  assert.equal(money(0), "$0.000000");
  assert.equal(money(null), "Unknown");
  assert.throws(() => money(-1));
  assert.throws(() => money(1.2));
});
test("missing credentials are explicit", async () =>
  assert.equal((await gateway(request("tasks"), undefined)).status, 503));
test("forbidden destinations, admin, traversal, events and methods cannot reach upstream", async () => {
  let n = 0;
  const f = (async () => {
    n++;
    return Response.json({});
  }) as typeof fetch;
  for (const p of [
    "admin",
    "tasks/" + uuid + "/resolve",
    "tasks/" + uuid + "/events",
    "https://example.com",
    "tasks/" + uuid + "/../admin",
  ])
    assert.equal((await gateway(request(p), "secret", f)).status, 404);
  assert.equal(
    (await gateway(request("tasks", "DELETE"), "secret", f)).status,
    405,
  );
  assert.equal(n, 0);
});
test("writes require same origin and bounded JSON before dispatch", async () => {
  let n = 0;
  const f = (async () => {
    n++;
    return Response.json({});
  }) as typeof fetch;
  for (const origin of [undefined, "https://attacker.test"])
    assert.equal(
      (await gateway(request("tasks", "POST", origin, "{}"), "secret", f))
        .status,
      403,
    );
  assert.equal(
    (
      await gateway(
        request("tasks", "POST", "http://localhost:3000", "x".repeat(17000)),
        "secret",
        f,
      )
    ).status,
    413,
  );
  assert.equal(n, 0);
});
test("one physical request, fixed destination, no redirects, secret server side only", async () => {
  let n = 0;
  const f = (async (url: unknown, init?: RequestInit) => {
    n++;
    assert.equal(String(url), "http://127.0.0.1:8000/v1/tasks");
    assert.equal(
      new Headers(init?.headers).get("authorization"),
      "Bearer secret",
    );
    assert.equal(init?.redirect, "error");
    assert.ok(init?.signal);
    return Response.json({ items: [] }, { headers: { "x-private": "hidden" } });
  }) as typeof fetch;
  const result = await gateway(request("tasks"), "secret", f);
  assert.equal(result.status, 200);
  assert.equal(result.headers.get("x-private"), null);
  assert.equal(n, 1);
});
test("stable mutation identities are forwarded exactly once after ambiguous failure", async () => {
  let n = 0;
  const f = (async (_url: unknown, init?: RequestInit) => {
    n++;
    assert.equal(
      new Headers(init?.headers).get("idempotency-key"),
      "stable-command-key",
    );
    throw new Error("secret transport details");
  }) as typeof fetch;
  const result = await gateway(
    request(
      "tasks",
      "POST",
      "http://localhost:3000",
      JSON.stringify({
        workflow: { name: "analysis", version: "1" },
        input: { texts: ["Synthetic"] },
      }),
    ),
    "secret",
    f,
  );
  assert.equal(result.status, 502);
  assert.ok(!(await result.text()).includes("secret"));
  assert.equal(n, 1);
});
test("upstream denial stays a denial; malformed and oversized upstream are explicit", async () => {
  assert.equal(
    (
      await gateway(request("tasks"), "secret", (async () =>
        Response.json(
          { detail: "Forbidden" },
          { status: 403 },
        )) as typeof fetch)
    ).status,
    403,
  );
  for (const body of ["broken", "x".repeat(1048577)])
    assert.equal(
      (
        await gateway(
          request("tasks"),
          "secret",
          (async () => new Response(body)) as typeof fetch,
        )
      ).status,
      502,
    );
});
test("command identity and exact body survive a tab reload", async () => {
  const { restoreCommand } = await import("../lib/platform.ts");
  const value = {
    path: "tasks",
    body: '{"input":{"texts":["synthetic"]}}',
    key: "retained-key",
    epoch: uuid,
  };
  assert.deepEqual(restoreCommand(JSON.stringify(value)), value);
  assert.equal(restoreCommand("{broken"), null);
  assert.equal(
    restoreCommand(JSON.stringify({ ...value, path: "admin" })),
    null,
  );
});
test("estimated charges are visible independently of confirmed and exposure", async () => {
  const { costRows } = await import("../lib/platform.ts");
  assert.deepEqual(
    costRows({
      confirmed_micro_usd: 0,
      estimated_micro_usd: 1000,
      uncertain_micro_usd: 0,
    }),
    [
      ["Confirmed", "$0.000000"],
      ["Estimated charge", "$0.001000"],
      ["Uncertain exposure", "$0.000000"],
    ],
  );
  assert.equal(costRows(undefined)[1][1], "Unknown");
});

test("definitive rejection releases the form; transport ambiguity retains the same command", async () => {
  const { retainCommand } = await import("../lib/platform.ts");
  for (const status of [400, 401, 403, 404, 409, 410, 413, 415, 422, 429])
    assert.equal(retainCommand(status), false);
  for (const status of [undefined, 408, 500, 502, 503])
    assert.equal(retainCommand(status), true);
});

test("free delivery refuses paid workflow versions before any physical request", async () => {
  const send = (version: string) =>
    request(
      "tasks",
      "POST",
      "http://localhost:3000",
      JSON.stringify({
        workflow: { name: "analysis", version },
        input: { texts: ["Synthetic"] },
      }),
    );
  let n = 0;
  const f = (async () => {
    n++;
    return Response.json({ task_id: uuid }, { status: 202 });
  }) as typeof fetch;
  assert.equal((await gateway(send("2"), "secret", f)).status, 403);
  assert.equal(n, 0);
  assert.equal((await gateway(send("1"), "secret", f)).status, 202);
  assert.equal(n, 1);
});
