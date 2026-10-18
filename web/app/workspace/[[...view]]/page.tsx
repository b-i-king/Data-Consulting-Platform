// "/workspace" — the signed-in workspace: a Copilot Money-style left pane of the
// services on the account's plan. All logic lives in <Workspace />.
//
// The folder name [[...view]] is Next.js for "this page also answers every
// address below it": /workspace/dashboard, /workspace/data-cleaning,
// /workspace/settings/billing ... all load this same page, and <Workspace />
// reads the address to open that section (the address map is lib/routes.ts).

import type { Metadata } from "next";
import Workspace from "@/components/workspace/Workspace";

export const metadata: Metadata = { title: "Workspace" };

export default function WorkspacePage() {
  return <Workspace />;
}
