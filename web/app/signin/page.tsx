// "/signin" — sign in, or create an account, with an email address.
// Reached from the home page ("Sign in" / "Create an account"). After either,
// the visitor lands in their workspace at /app. `?mode=signup` opens the
// create-account form; anything else opens sign-in.

import type { Metadata } from "next";
import SignInPanel from "@/components/auth/SignInPanel";

export const metadata: Metadata = { title: "Sign in" };

export default async function SignInPage({ searchParams }: { searchParams: Promise<{ mode?: string }> }) {
  const { mode } = await searchParams;
  return <SignInPanel initialMode={mode === "signup" ? "signup" : "signin"} />;
}
