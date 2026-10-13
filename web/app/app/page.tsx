// "/app" — the signed-in workspace: a Copilot Money-style left pane of the
// services on the account's plan. All logic lives in <Workspace />.

import type { Metadata } from "next";
import Workspace from "@/components/workspace/Workspace";

export const metadata: Metadata = { title: "Workspace" };

export default function AppPage() {
  return <Workspace />;
}
