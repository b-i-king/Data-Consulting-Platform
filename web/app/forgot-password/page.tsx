// "/forgot-password" — reached from "Forgot password?" on the sign-in form.
// The person types their email; we send a reset link (Supabase emails it).
// The reply is the same whether or not the email has an account, so the page
// can't be used to find out who signed up (api/services/auth.py).

import type { Metadata } from "next";
import ForgotPasswordForm from "@/components/auth/ForgotPasswordForm";

export const metadata: Metadata = { title: "Forgot password" };

export default function ForgotPasswordPage() {
  return <ForgotPasswordForm />;
}
