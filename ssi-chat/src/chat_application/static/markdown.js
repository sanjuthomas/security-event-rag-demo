function escapeHtml(text) {
  return String(text)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function parseTableRow(line) {
  const trimmed = line.trim();
  if (!trimmed.startsWith("|") || !trimmed.endsWith("|")) {
    return null;
  }
  return trimmed
    .slice(1, -1)
    .split("|")
    .map((cell) => cell.trim().replace(/\\\|/g, "|"));
}

function isTableSeparator(line) {
  const cells = parseTableRow(line);
  if (!cells) {
    return false;
  }
  return cells.every((cell) => /^:?-{1,}:?$/.test(cell));
}

function buildTableHtml(header, rows) {
  const thead = header.map((cell) => `<th>${escapeHtml(cell)}</th>`).join("");
  const tbody = rows
    .map(
      (row) =>
        `<tr>${row.map((cell) => `<td>${escapeHtml(cell)}</td>`).join("")}</tr>`
    )
    .join("");
  return `<div class="table-wrap"><table class="md-table"><thead><tr>${thead}</tr></thead><tbody>${tbody}</tbody></table></div>`;
}

function renderAssistantMarkdown(text) {
  const lines = String(text || "").split("\n");
  const parts = [];
  let index = 0;

  while (index < lines.length) {
    const header = parseTableRow(lines[index]);
    if (header && index + 1 < lines.length && isTableSeparator(lines[index + 1])) {
      index += 2;
      const rows = [];
      while (index < lines.length) {
        const row = parseTableRow(lines[index]);
        if (!row) {
          break;
        }
        rows.push(row);
        index += 1;
      }
      parts.push(buildTableHtml(header, rows));
      continue;
    }

    const plain = [];
    while (index < lines.length) {
      const maybeHeader = parseTableRow(lines[index]);
      if (maybeHeader && index + 1 < lines.length && isTableSeparator(lines[index + 1])) {
        break;
      }
      plain.push(lines[index]);
      index += 1;
    }

    if (plain.length) {
      parts.push(`<div class="md-text">${escapeHtml(plain.join("\n"))}</div>`);
    }
  }

  return parts.join("");
}
