// "/signin" — sign in, or create an account, with an email address.
// Reached from the home page ("Sign in"). After either, the visitor lands in
// their workspace at /workspace — or back on the workspace page they were
// trying to open (`?next=/workspace/...`). `?mode=signup` opens the
// create-account form (so does /signup); anything else opens sign-in.

import type { Metadata } from "next";
import SignInPanel from "@/components/auth/SignInPanel";

export const metadata: Metadata = { title: "Sign in" };

export default async function SignInPage({ searchParams }: { searchParams: Promise<{ mode?: string; next?: string }> }) {
  const { mode, next } = await searchParams;
  return <SignInPanel initialMode={mode === "signup" ? "signup" : "signin"} next={next} />;
}
