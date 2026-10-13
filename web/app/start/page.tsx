// "/start" — the guest intake questionnaire. All logic lives in <Wizard />.

import type { Metadata } from "next";
import Wizard from "@/components/wizard/Wizard";

export const metadata: Metadata = { title: "Get started" };

export default function StartPage() {
  return <Wizard />;
}
