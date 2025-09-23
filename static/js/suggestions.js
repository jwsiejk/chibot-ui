const MAX_CHIPS = 4;
const MAX_WORDS = 7;

function normalizeSuggestion(item){
  if (typeof item === "string") {
    return item;
  }

  if (item && typeof item === "object") {
    if (typeof item.label === "string") {
      return item.label;
    }

    for (const value of Object.values(item)) {
      if (typeof value === "string") {
        return value;
      }
    }
  }

  return String(item ?? "");
}

export function renderSuggestions(suggestions = [], onClick){
  const wrap = document.getElementById("suggestions");
  if (!wrap) return;
  wrap.innerHTML = "";
  const trimmed = suggestions
    .slice(0, MAX_CHIPS)
    .map(normalizeSuggestion)
    .map(s => s.trim())
    .map(s => s.split(/\s+/).slice(0, MAX_WORDS).join(" "));

  for (const s of trimmed){
    const chip = document.createElement("button");
    chip.className = "chip";
    chip.type = "button";
    chip.textContent = s;
    chip.addEventListener("click", () => onClick?.(s));
    wrap.appendChild(chip);
  }
}
