// "/reset-password" — where the emailed reset link lands. The person chooses a
// new password (typed twice). The link's token arrives in the address:
//   * Supabase: after "#" — #access_token=...&type=recovery
//   * local mode (no Supabase): ?token=lr_...
// Add this page's full address to Supabase -> Authentication -> URL
// Configuration -> Redirect URLs.

import type { Metadata } from "next";
import ResetPasswordForm from "@/components/auth/ResetPasswordForm";

export const metadata: Metadata = { title: "Reset password", robots: { index: false } };

export default function ResetPasswordPage() {
  return <ResetPasswordForm />;
}
