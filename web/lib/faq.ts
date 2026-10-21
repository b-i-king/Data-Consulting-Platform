// ── Frequently asked questions ────────────────────────────────────────────────
// One list, shown in two places: the home page (with search-engine markup, so
// Google can show the answers) and Settings -> Help & FAQ for signed-in people
// (components/FaqSection.tsx). Keep answers short, plain and true to how the
// platform works today — prices change in the Admin panel, so answers point to
// Plan & billing instead of quoting numbers.

export interface Faq {
  q: string;
  a: string;
}

export const FAQS: Faq[] = [
  {
    q: "What does this platform do?",
    a: "It helps small and growing businesses get their data in shape and put it to work: automatic cleaning of spreadsheets, dashboards, analysis, data storage and syncing, machine learning and plain-English AI questions — plus a real data consultant when you want one. It connects businesses that need data help with consultants who can give it.",
  },
  {
    q: "Can I try it without an account?",
    a: "Yes. Start as a guest, upload a spreadsheet and see it cleaned and summarized in minutes. You only need an account to save your work, download files or add tools.",
  },
  {
    q: "Do you keep my data?",
    a: "No, not unless you ask us to. Files you upload are processed in memory and gone when you're done. Storing your data is an opt-in part of Data Engineering: you see a preview first and say yes before anything is saved, and you can delete it any time.",
  },
  {
    q: "How much does it cost?",
    a: "Cleaning and previewing your data is free. Downloading the cleaned files is pay-as-you-go (by processing minute), or included with a plan. The other tools are monthly or yearly subscriptions you build yourself, or a bundle (Starter, Growth or Pro) that costs less than the tools separately. Yearly plans cost less than paying monthly. You can cancel any time and keep access until the end of the period you paid for. Today's prices are on the Plan & billing page.",
  },
  {
    q: "What kinds of files can I upload?",
    a: "CSV, Excel and other common spreadsheet and data formats, including several files at once (they're sorted and stacked for you). For safety, file types that can run code are never opened.",
  },
  {
    q: "Can I connect my business tools or my cloud database?",
    a: "Yes — they work a little differently. Business tools (the accounting, invoicing and sales apps small businesses use) connect with your own sign-in on their site, with any paid plan; we read your records live, and storing them on a schedule for history and trends comes with Data Engineering. On the free plan, download a report or list from the tool and upload it here — the analysis is the same. A cloud database (your own PostgreSQL, such as Supabase) is free to connect: we read the tables where they live and copy nothing. One note: some tools' terms don't allow their data to be used with AI or machine learning, so for data from those tools the AI and machine-learning features are turned off — cleaning, exploring and dashboards still work.",
  },
  {
    q: "How do I work with a consultant?",
    a: "Most services start with a free 30-minute consultation. After that you can book hours, or add ongoing help billed monthly or yearly. Consultants are screened before they join, and you pay them through the platform.",
  },
  {
    q: "What is Beacon?",
    a: "Beacon is the AI assistant: ask a question about your data in plain English and get the answer with the calculation shown, so you can check it. Everyone gets free questions each month; the AI add-on gives you more.",
  },
  {
    q: "Is there a mobile app?",
    a: "Not yet, and you don't need one: the website works on phones and tablets, in portrait and landscape. If you'd like it on your home screen, use your browser's \"Add to Home Screen\" option.",
  },
  {
    q: "How can my business appear in \"Trusted by\"?",
    a: "Businesses on a paid plan for at least 30 days, with their own website domain, can send their logo from Settings -> Account: just the logo on a transparent background (a plain background is removed for you). It's shown in the site's colors so the row looks consistent, a person checks every logo (clean and family-friendly only) before it goes live, and it links to your website. It's shown while your plan is paid and comes off automatically if you cancel.",
  },
];
