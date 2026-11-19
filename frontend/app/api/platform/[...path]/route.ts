import { gateway } from "../../../../lib/platform";
export const dynamic = "force-dynamic";
export const GET = (request: Request) =>
  gateway(request, process.env.ATP_API_KEY);
export const POST = GET;
