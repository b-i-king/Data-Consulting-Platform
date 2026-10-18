// "/signup" — create an account with an email address. The same form as
// /signin (components/auth/SignInPanel.tsx), opened on "Create an account";
// it can switch to signing in. After either, the visitor lands in /workspace
// (or the workspace page in `?next=/workspace/...`).

import type { Metadata } from "next";
import SignInPanel from "@/components/auth/SignInPanel";

export const metadata: Metadata = { title: "Create an account" };

export default async function SignUpPage({ searchParams }: { searchParams: Promise<{ next?: string }> }) {
  const { next } = await searchParams;
  return <SignInPanel initialMode="signup" next={next} />;
}
