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

// Service category → icon (components/ui/Icon.tsx), shown next to each service
// in the workspace's left pane so each part of the product is easy to spot. Line
// icons in the theme's colors, not emoji (emoji look different on every device
// and never match the theme).
export const CATEGORY_ICON: Record<string, string> = {
  data_visualization: "chart",
  data_cleaning: "sparkles",
  data_strategy_discovery: "compass",
  data_exploration_analysis: "search",
  data_engineering: "database",
  data_science_ml: "trend",
  ai: "beacon",
};
