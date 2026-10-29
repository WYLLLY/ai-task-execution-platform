export function canCancel(state: string): boolean {
  return ["queued", "running", "suspended"].includes(state);
}
export function money(value: number | null): string {
  if (value === null) return "Unknown";
  if (!Number.isSafeInteger(value) || value < 0)
    throw new Error("Invalid micro-USD");
  return `$${Math.floor(value / 1000000)}.${String(value % 1000000).padStart(6, "0")}`;
}
async function bounded(
  body: ReadableStream<Uint8Array> | null,
  limit: number,
): Promise<string> {
  if (!body) return "";
  const reader = body.getReader();
  const chunks: Uint8Array[] = [];
  let size = 0;
  try {
    while (true) {
      const next = await reader.read();
      if (next.done) break;
      size += next.value.length;
      if (size > limit) {
        await reader.cancel();
        throw new Error("Size limit");
      }
      chunks.push(next.value);
    }
  } finally {
    reader.releaseLock();
  }
  const bytes = new Uint8Array(size);
  let offset = 0;
  for (const chunk of chunks) {
    bytes.set(chunk, offset);
    offset += chunk.length;
  }
  return new TextDecoder("utf-8", { fatal: true }).decode(bytes);
}
export async function gateway(
  request: Request,
  key: string | undefined,
  transport: typeof fetch = fetch,
): Promise<Response> {
  const url = new URL(request.url);
  const path = url.pathname.replace(/^\/api\/platform\//, "");
  const id = "[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}";
  const readable = new RegExp(
    `^(system|workflows|usage|tasks(?:/${id}(?:/steps)?)?)$`,
  ).test(path);
  const writable =
    path === "tasks" || new RegExp(`^tasks/${id}/cancel$`).test(path);
  const error = (status: number, message: string) =>
    Response.json(
      { error: message },
      { status, headers: { "cache-control": "no-store" } },
    );
  if (!readable && !writable) return error(404, "Unsupported route");
  if (
    !["GET", "POST"].includes(request.method) ||
    (request.method === "GET" && !readable) ||
    (request.method === "POST" && !writable)
  )
    return error(405, "Unsupported method");
  if (!["localhost", "127.0.0.1", "[::1]"].includes(url.hostname))
    return error(403, "Local use only");
  const authority = request.headers.get("host") ?? url.host;
  if (!/^(localhost|127\.0\.0\.1|\[::1\])(?::[0-9]{1,5})?$/.test(authority))
    return error(403, "Local host required");
  let browserOrigin: string;
  try {
    browserOrigin = new URL(`${url.protocol}//${authority}`).origin;
  } catch {
    return error(403, "Invalid local authority");
  }
  if (
    request.method === "POST" &&
    request.headers.get("origin") !== browserOrigin
  )
    return error(403, "Same origin required");
  if (!key) return error(503, "Local API credential is not configured");
  const headers = new Headers({
    authorization: `Bearer ${key}`,
    accept: "application/json",
  });
  let body: string | undefined;
  if (request.method === "POST") {
    if (!request.headers.get("content-type")?.startsWith("application/json"))
      return error(415, "JSON required");
    try {
      body = await bounded(request.body, 16384);
    } catch {
      return error(413, "Request too large");
    }
    try {
      const payload = JSON.parse(body);
      if (
        path === "tasks" &&
        (payload?.workflow?.name !== "analysis" ||
          payload?.workflow?.version !== "1")
      )
        return error(
          403,
          "Free local mode allows only the simulated analysis workflow v1",
        );
    } catch {
      return error(400, "Invalid JSON");
    }
    for (const name of ["idempotency-key", "x-execution-epoch"]) {
      const value = request.headers.get(name);
      if (!value || value.length > 128)
        return error(400, "Command identity required");
      headers.set(name, value);
    }
    headers.set("content-type", "application/json");
  }
  for (const name of url.searchParams.keys())
    if (!["limit", "cursor"].includes(name))
      return error(400, "Unsupported query");
  try {
    const upstream = await transport(
      `http://127.0.0.1:8000/v1/${path}${url.search}`,
      {
        method: request.method,
        headers,
        body,
        redirect: "error",
        cache: "no-store",
        signal: AbortSignal.timeout(15000),
      },
    );
    const value = JSON.parse(await bounded(upstream.body, 1048576));
    return Response.json(value, {
      status: upstream.status,
      headers: { "cache-control": "no-store" },
    });
  } catch {
    return error(
      502,
      "Local API request failed. A sent command may have committed; retry only with the same command identity.",
    );
  }
}
export type PendingCommand = {
  path: string;
  body: string;
  key: string;
  epoch: string;
};
export function restoreCommand(value: string | null): PendingCommand | null {
  if (!value || value.length > 20000) return null;
  try {
    const v = JSON.parse(value);
    if (
      !v ||
      typeof v.path !== "string" ||
      !/^(tasks|tasks\/[0-9a-f-]{36}\/cancel)$/.test(v.path) ||
      typeof v.body !== "string" ||
      v.body.length > 16384 ||
      typeof v.key !== "string" ||
      !/^[a-zA-Z0-9-]{1,128}$/.test(v.key) ||
      typeof v.epoch !== "string" ||
      !/^[0-9a-f-]{36}$/.test(v.epoch)
    )
      return null;
    JSON.parse(v.body);
    return { path: v.path, body: v.body, key: v.key, epoch: v.epoch };
  } catch {
    return null;
  }
}
export function costRows(
  costs:
    | {
        confirmed_micro_usd: number;
        estimated_micro_usd?: number;
        uncertain_micro_usd: number;
      }
    | undefined,
): string[][] {
  return [
    ["Confirmed", money(costs?.confirmed_micro_usd ?? null)],
    ["Estimated charge", money(costs?.estimated_micro_usd ?? null)],
    ["Uncertain exposure", money(costs?.uncertain_micro_usd ?? null)],
  ];
}

export function retainCommand(status: number | undefined): boolean {
  return status === undefined || status === 408 || status >= 500;
}
