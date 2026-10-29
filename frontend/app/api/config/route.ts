export const dynamic = "force-dynamic";
export function GET() {
  return Response.json(
    { configured: Boolean(process.env.ATP_API_KEY) },
    { headers: { "cache-control": "no-store" } },
  );
}
