import { credentialBinding } from "../../../lib/platform";

export const dynamic = "force-dynamic";
export async function GET(request: Request) {
  const { hostname } = new URL(request.url);
  const host = request.headers.get("host");
  if (
    !["localhost", "127.0.0.1", "[::1]"].includes(hostname) ||
    (host !== null &&
      !/^(localhost|127\.0\.0\.1|\[::1\])(?::[0-9]{1,5})?$/.test(host))
  ) {
    return Response.json({ configured: false }, { status: 403 });
  }
  const key = process.env.ATP_API_KEY;
  return Response.json(
    {
      configured: Boolean(key),
      credential: key ? await credentialBinding(key) : null,
    },
    { headers: { "cache-control": "no-store" } },
  );
}
