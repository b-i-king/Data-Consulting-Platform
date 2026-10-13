// Service category → accent color (CSS variable from app/globals.css).
// Gives each category a consistent color everywhere it appears — service
// cards, chips, and badges — like category colors in a budgeting app.

const CATEGORY_COLORS: Record<string, string> = {
  data_cleaning: "var(--color-cat-cleaning)",
  data_strategy_discovery: "var(--color-cat-strategy)",
  data_exploration_analysis: "var(--color-cat-analysis)",
  data_visualization: "var(--color-cat-viz)",
  data_science_ml: "var(--color-cat-ml)",
  data_engineering: "var(--color-cat-engineering)",
  ai: "var(--color-cat-ai)",
};

export const categoryColor = (serviceId: string) => CATEGORY_COLORS[serviceId] ?? "var(--color-brand)";

// Service category → emoji, shown next to each service in the workspace's left
// pane (and in skill menus) so each part of the product is easy to spot.
const CATEGORY_EMOJI: Record<string, string> = {
  data_visualization: "📊",
  data_cleaning: "🧹",
  data_strategy_discovery: "🧭",
  data_exploration_analysis: "🔍",
  data_engineering: "🏗️",
  data_science_ml: "🔮",
  ai: "🤖",
};

export const categoryEmoji = (serviceId: string) => CATEGORY_EMOJI[serviceId] ?? "•";
