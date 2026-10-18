// "/get-started" — the guest intake questionnaire. All logic lives in <Wizard />.
//
// The folder name [[...step]] is Next.js for "this page also answers every
// address below it": /get-started/about-you, /get-started/upload,
// /get-started/your-needs ... all load this same page, and <Wizard /> shows
// that step (the address map is lib/routes.ts).

import type { Metadata } from "next";
import Wizard from "@/components/wizard/Wizard";

export const metadata: Metadata = { title: "Get started" };

export default function GetStartedPage() {
  return <Wizard />;
}
