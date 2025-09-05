const MAX_CHIPS = 4;
const MAX_WORDS = 7;

export function renderSuggestions(suggestions = [], onClick){
  const wrap = document.getElementById("suggestions");
  if (!wrap) return;
  wrap.innerHTML = "";
  const trimmed = suggestions
    .slice(0, MAX_CHIPS)
    .map(s => s.toString().trim())
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