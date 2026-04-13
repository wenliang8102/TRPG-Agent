// ==================== 关键词高亮（作用于纯文本） ====================
function highlightKeywords(text: string): string {
  // 1. 去掉所有的 ** 标记
  let result = text.replace(/\*\*/g, '');
  
  // 2. 删除所有的 · 字符
  result = result.replace(/·/g, '');

  // 3. 匹配 数字. 后面的所有内容（直到行尾）并高亮
  //    支持数字后跟 . 、． 、、，可选空格，然后捕获剩余所有字符
  const patterns: [RegExp, string][] = [
    [/(\d+[\.．、])\s*(.+)$/gm, '$1<span class="rpg-keyword-yellow">$2</span>'],
  ];

  for (const [regex, replacement] of patterns) {
    result = result.replace(regex, replacement);
  }

  return result;
}

// ==================== 列表适配 ====================
export function adaptLLMOutput(text: string): string {
  const lines = text.split(/\r?\n/);
  const result: string[] = [];

  for (let i = 0; i < lines.length; i++) {
    let line = lines[i];

    // 有序列表模式：行首为 数字 + 点/顿号/右括号 + 可选空格
    const orderedMatch = line.match(/^(\d+)([\.、\)）])\s*(.*)$/);
    if (orderedMatch) {
      // 保留完整的行内容（数字、点号、内容），后续高亮会处理内容
      const fullLine = orderedMatch[1] + orderedMatch[2] + ' ' + orderedMatch[3];
      const highlighted = highlightKeywords(fullLine);
      result.push(`<div class="custom-list-item ordered">${highlighted}</div>`);
      continue;
    }

    // 删除行首的 -（允许前导空格），且不保留该符号
    const dashMatch = line.match(/^(\s*)-/);
    if (dashMatch) {
      let remaining = line.slice(dashMatch[0].length);
      if (remaining.startsWith(' ')) {
        remaining = remaining.slice(1);
      }
      line = remaining;
      // 删除后，该行继续走普通逻辑（可能变成空行或普通文本）
    }

    // 无序列表模式（仅处理 * 和 +，因为 - 已经被删除了）
    const unorderedMatch = line.match(/^\s*([*+])\s+(.*)$/);
    if (unorderedMatch) {
      const fullLine = unorderedMatch[1] + ' ' + unorderedMatch[2];
      const highlighted = highlightKeywords(fullLine);
      result.push(`<div class="custom-list-item unordered">${highlighted}</div>`);
      continue;
    }

    // 普通行
    const highlightedLine = highlightKeywords(line);
    result.push(highlightedLine);
  }

  return result.join('\n');
}

// ==================== 辅助函数：HTML 转义 ====================
function escapeHtml(str: string): string {
  return str
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}