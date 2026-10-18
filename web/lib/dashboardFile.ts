// The "Download (HTML)" file, built entirely in the browser.
//
// The dashboard on screen already holds every number the file needs (the
// build-dashboard skill's data: all ranges and comparisons). So instead of
// asking the server to rebuild it, we fetch the empty template — a static file
// Vercel serves for free, web/public/dashboard-template.html — and paste the
// title and data in. Same result as render_html in
// data_skills/build-dashboard/skill.py, with no Render time and no Supabase reads.
//
// The template is a copy of data_skills/build-dashboard/templates/; after
// changing those, run `python scripts/sync_dashboard_template.py` (pytest checks).

/**
 * Build the self-contained dashboard HTML from the skill's data.
 *
 * @param data  the build-dashboard result's `data` (what the dashboard draws)
 * @param title the dashboard's title (shown in the browser tab)
 * @returns the HTML file's text
 */
export async function buildDashboardHtml(data: unknown, title: string): Promise<string> {
  const res = await fetch("/dashboard-template.html");
  if (!res.ok) throw new Error("We couldn't get the dashboard template. Please try again.");
  const page = await res.text();
  // "</" is written "<\/" so a "</script>" inside the data can't end the data block early.
  const safe = JSON.stringify(data).replaceAll("</", "<\\/");
  const safeTitle = (title || "At a glance").replaceAll("<", "&lt;");
  // A function as the replacement, so "$" in the data is kept as-is (a plain
  // string would treat "$&" and similar as special patterns).
  return page.replaceAll("__TITLE__", () => safeTitle).replaceAll("__DATA__", () => safe);
}
