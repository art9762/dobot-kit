"use strict";

for (const block of document.querySelectorAll(".codeblock")) {
  const header = block.querySelector(".code-head");
  const code = block.querySelector("pre code");
  if (!header || !code || !navigator.clipboard?.writeText) continue;

  const button = document.createElement("button");
  button.type = "button";
  button.className = "copy-btn";
  button.textContent = "Копировать";
  button.setAttribute("aria-live", "polite");
  let reset;
  button.addEventListener("click", async () => {
    clearTimeout(reset);
    try {
      await navigator.clipboard.writeText(code.textContent);
      button.textContent = "Скопировано";
    } catch {
      const range = document.createRange();
      range.selectNodeContents(code);
      const selection = window.getSelection();
      selection.removeAllRanges();
      selection.addRange(range);
      button.textContent = "Выделено для копирования";
    }
    reset = setTimeout(() => { button.textContent = "Копировать"; }, 2200);
  });
  header.append(button);
}
