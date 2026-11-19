import type { Metadata } from "next";
import "./style.css";
export const metadata: Metadata = {
  title: "Taskroom — AI Task Execution Platform",
  description:
    "A local workspace for durable AI execution. Sample preview and local API access.",
};
export default function Layout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
