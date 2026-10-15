// The preview image for links shared on X (Twitter): the same picture as the
// Open Graph image (app/opengraph-image.tsx). Next.js needs its own file
// name to add the "twitter:image" tag, so this file just re-uses that one.
// (Route settings are written out here: Next.js reads them from each file directly.)

import OpengraphImage from "./opengraph-image";

export const alt = "Data consulting for small businesses";
export const size = { width: 1200, height: 630 };
export const contentType = "image/png";

export default OpengraphImage;
