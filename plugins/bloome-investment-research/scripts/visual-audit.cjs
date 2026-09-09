// Full-company baseline measured from the user-approved NVIDIA Chinese report:
// 27 figure components: 19 quantitative (including a sensitivity matrix),
// 5 causal diagrams, and 3 reference tables. Count figures, not nested SVGs.
function auditVisuals(markdown, specification, coverage = {}) {
  const list = Array.isArray(specification) ? specification : specification?.visuals || [];
  const markers = [...markdown.matchAll(/\{\{visual:([a-z0-9][a-z0-9_-]*)\}\}/gi)].map((match) => match[1]);
  const placed = list.filter((visual) => markers.includes(visual.key));
  const quantitative = placed.filter((visual) => ["bar", "line", "range", "waterfall", "stacked", "donut"].includes(visual.type) ||
    (visual.type === "matrix" && visual.rows?.length && visual.rows.every((row) => row.values?.length && row.values.every((value) => /^[-+]?\d[\d,.]*\s*[%x倍]?$/.test(String(value).trim())))));
  const counts = { total:placed.length, quantitative:quantitative.length, causal:placed.filter((v) => v.type === "flow").length,
    tables:placed.filter((v) => v.type === "table").length, grammars:new Set(placed.map((v) => v.type)).size };
  const errors = [];
  const warnings = [];
  if (counts.total < 15) warnings.push("Visual coverage review: a full institutional report usually benefits from 15–20 substantive figures. Review unvisualized trends, composition, expectations, valuation and causal mechanisms; this is not a numeric delivery gate");
  const identities = new Set();
  for (const visual of placed) {
    const identity = JSON.stringify([visual.type, visual.items, visual.series, visual.nodes, visual.columns, visual.rows]);
    if (identities.has(identity)) errors.push(`Duplicate visual data/geometry: ${visual.key}; renamed copies do not increase coverage`);
    identities.add(identity);
  }
  if (/(?:\{\{visual:[^}]+\}\}\s*){3,}/.test(markdown)) warnings.push("Three or more consecutive figures: insert a natural interpretive paragraph between chart groups; do not prefix it with 观点 or highlight it");
  return { counts, errors, warnings };
}
module.exports = { auditVisuals };
