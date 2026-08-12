from __future__ import annotations

import re
from typing import TYPE_CHECKING

from .models import DomField

if TYPE_CHECKING:
    from playwright.sync_api import Page


_NUMERIC_LABEL_RE = re.compile(
    r"(?:比例|百分比|金额|出资额|数量|回报率|收益率|人数|价格|单价|总价|"
    r"额度|估值|面积|年限|期限|周期(?:[（(]?(?:年|月|天)[）)]?)?|天数|月数|"
    r"次数|排序|序号)"
)
_NUMERIC_CODE_RE = re.compile(
    r"(?:amount|rate|ratio|percent|percentage|quantity|count|price|totalprice|"
    r"quota|valuation|area|duration|period(?:month|year|day)?|days?|months?|years?)$",
    re.IGNORECASE,
)


def is_semantic_numeric_field(field_code: str, label: str) -> bool:
    """Recognize business-number text inputs before generating a baseline value."""
    normalized_label = re.sub(r"^(?:请)?(?:输入|填写)", "", label or "").strip()
    return bool(
        _NUMERIC_LABEL_RE.search(normalized_label)
        or _NUMERIC_CODE_RE.search(field_code or "")
    )


DOM_FIELD_SCRIPT = r"""
(providedRoot) => {
  const visible = (el) => {
    if (!(el instanceof HTMLElement)) return false;
    if (el.closest('[hidden], [aria-hidden="true"]')) return false;
    const style = getComputedStyle(el);
    const rect = el.getBoundingClientRect();
    return style.display !== 'none' && style.visibility !== 'hidden' && rect.width > 0 && rect.height > 0;
  };
  const dialogs = [...document.querySelectorAll('[role="dialog"],.el-dialog,.el-drawer,.ant-modal,.ant-drawer')].filter(visible);
  const root = providedRoot || dialogs.at(-1) || document;
  const visibleControl = (el) => {
    if (visible(el)) return true;
    if (!(el instanceof HTMLInputElement) || el.type !== 'file') return false;
    const upload = el.closest('.el-upload,.ant-upload,.purvar-upload') ||
      el.parentElement?.closest('[class*="upload"]');
    return visible(upload || el.parentElement);
  };
  const componentControlSelector = '.el-select,.ant-select,.el-date-editor,.el-input-number,.ant-input-number,.el-radio-group,.el-radio,.el-checkbox-group,.el-checkbox,.el-switch';
  const componentOwner = (el) => {
    const choiceGroup = el.closest('.el-radio-group,.el-checkbox-group');
    return choiceGroup || el.closest(componentControlSelector) || el;
  };
  const disabledStateSelector = [
    '[disabled]', '[aria-disabled="true"]', '.is-disabled', '.el-is-disabled',
    '.ant-select-disabled', '.ant-radio-wrapper-disabled', '.ant-checkbox-wrapper-disabled'
  ].join(',');
  const disabledOf = (el) => {
    const owner = componentOwner(el);
    const formItem = owner.closest(
      '.el-form-item,.ant-form-item,.purvar_form_item,[class*="form-item"]'
    );
    return [el, owner, formItem].some((node) => node instanceof HTMLElement && (
      node.matches(disabledStateSelector) || !!node.closest(disabledStateSelector)
    ));
  };
  const controls = [...root.querySelectorAll(
    `input:not([type="hidden"]),textarea,select,[contenteditable="true"],` +
    `[role="combobox"],[role="switch"],[role="checkbox"],[role="radio"],` +
    componentControlSelector
  )].filter(visibleControl);
  const cleanLabel = (value) => (value || '').replace(/^\s*\*\s*/, '').replace(/\s+/g, ' ').trim();
  const optionLabelSelector = '.el-radio,.el-checkbox,.ant-radio-wrapper,.ant-checkbox-wrapper,[role="radio"],[role="checkbox"]';
  const componentPlaceholder = (el) => {
    const owner = el.closest(componentControlSelector) || el;
    const node = owner.querySelector(
      '.el-select__placeholder,.el-input__inner[placeholder],input[placeholder],textarea[placeholder]'
    );
    return cleanLabel(node?.innerText || node?.textContent || node?.getAttribute?.('placeholder') || el.getAttribute('placeholder') || '');
  };
  const ariaLabel = (el) => {
    const ids = (el.getAttribute('aria-labelledby') || '').split(/\s+/).filter(Boolean);
    for (const id of ids) {
      const node = document.getElementById(id);
      const text = cleanLabel(node?.innerText || node?.textContent || '');
      if (text) return text;
    }
    return '';
  };
  const labelOf = (el) => {
    const labelled = ariaLabel(el);
    if (labelled) return labelled;
    const cell = el.closest('td');
    const table = cell?.closest('table');
    if (cell && table && cell.cellIndex >= 0) {
      const headerRows = [...table.querySelectorAll('thead tr')];
      const header = headerRows.at(-1)?.cells?.[cell.cellIndex];
      const headerText = cleanLabel(header?.innerText || header?.textContent || '');
      if (headerText && headerText !== '操作' && headerText !== '序号') return headerText;
    }
    const columnClass = cell ? [...cell.classList].find((name) => /_column_\d+$/.test(name)) : '';
    const tableRoot = cell?.closest('.el-table,.ant-table');
    if (columnClass && tableRoot) {
      const header = tableRoot.querySelector(`th.${CSS.escape(columnClass)}`);
      const headerText = cleanLabel(header?.innerText || header?.textContent || '');
      if (headerText && headerText !== '操作' && headerText !== '序号') return headerText;
    }
    const purvarItem = el.closest('.purvar_form_item');
    if (purvarItem) {
      // Purvar lays out the business label and control in sibling columns. The
      // selected value belongs to the content column and must not become the
      // field label when the component has no native <label> element.
      const directLabelColumn = purvarItem.querySelector(':scope > .el-col:first-child');
      const directLabel = (
        directLabelColumn && !directLabelColumn.contains(el)
          ? cleanLabel(directLabelColumn.innerText || directLabelColumn.textContent || '')
          : ''
      );
      if (directLabel) return directLabel;
      const independentLabels = [...purvarItem.querySelectorAll(
        '.el-form-item__label,.ant-form-item-label,.purvar_form_label,' +
        '.purvar-form-label,.purvar_form_item_title,' +
        '[class*="field-label"],[class*="form_label"],label'
      )].filter((node) => !node.closest(optionLabelSelector));
      const independentLabel = independentLabels
        .map((node) => cleanLabel(node.innerText || node.textContent || ''))
        .find(Boolean);
      if (independentLabel) return independentLabel;
    }
    const item = el.closest('.el-form-item,.ant-form-item') || el.closest('[class*="form-item"]');
    const node = [...(item?.querySelectorAll('.el-form-item__label,.ant-form-item-label,label,[class*="label"]') || [])]
      .filter((candidate) => !candidate.closest(optionLabelSelector))
      .find((candidate) => cleanLabel(candidate.innerText || candidate.textContent || ''));
    return cleanLabel(
      node?.innerText || node?.textContent ||
      componentPlaceholder(el) ||
      el.getAttribute('aria-label') ||
      el.getAttribute('placeholder') || ''
    );
  };
  const codeOf = (el, label = '') => {
    const collectionItem = el.closest('[data-ei-collection-item][data-ei-field-path]');
    const collectionPath = collectionItem?.getAttribute('data-ei-field-path') || '';
    const coded = el.closest('[data-field-code],[field-code]');
    const propItem = el.closest('.el-form-item[prop],.ant-form-item[prop],[class*="form-item"][prop]');
    const name = el.getAttribute('name') || '';
    const type = (el.getAttribute('type') || '').toLowerCase();
    const genericFileName = type === 'file' && /^(?:file|files?)$/i.test(name);
    const owner = componentOwner(el);
    const innerControl = owner === el
      ? owner.querySelector('input[id],textarea[id],[role="combobox"][id],[role="radio"][id],[role="checkbox"][id],input[name]')
      : null;
    // Element Plus generates a new input id on every render.  The form item
    // prop is the stable business field code and must win over generated name/id.
    return collectionPath || coded?.getAttribute('data-field-code') || coded?.getAttribute('field-code') ||
      propItem?.getAttribute('prop') ||
      (!genericFileName && !name.startsWith('el-id-') ? name : '') ||
      (type === 'file' && label ? `file:${label}` : '') ||
      el.id || innerControl?.id || innerControl?.getAttribute?.('name') || name;
  };
  const selectorOf = (el, code, label = '', kind = '') => {
    const type = (el.getAttribute('type') || '').toLowerCase();
    if (type === 'file' && code && !code.startsWith('el-id-')) {
      if (code.startsWith('file:') && label) {
        return `.purvar_form_item:has-text(${JSON.stringify(label)}) input[type="file"]`;
      }
      return `[data-field-code="${CSS.escape(code)}"] input[type="file"],` +
        `[field-code="${CSS.escape(code)}"] input[type="file"],` +
        `[prop="${CSS.escape(code)}"] input[type="file"]`;
    }
    if (code && !code.startsWith('el-id-') && (kind === 'select' || kind === 'multi_select')) {
      return `[data-field-code="${CSS.escape(code)}"] .el-select,` +
        `[field-code="${CSS.escape(code)}"] .el-select,` +
        `[prop="${CSS.escape(code)}"] .el-select,` +
        `[data-field-code="${CSS.escape(code)}"] [role="combobox"],` +
        `[field-code="${CSS.escape(code)}"] [role="combobox"],` +
        `[prop="${CSS.escape(code)}"] [role="combobox"]`;
    }
    if (code && !code.startsWith('el-id-') && kind === 'radio') {
      return `[data-field-code="${CSS.escape(code)}"] .el-radio-group,` +
        `[field-code="${CSS.escape(code)}"] .el-radio-group,` +
        `[prop="${CSS.escape(code)}"] .el-radio-group,` +
        `[data-field-code="${CSS.escape(code)}"] .el-radio,` +
        `[field-code="${CSS.escape(code)}"] .el-radio,` +
        `[prop="${CSS.escape(code)}"] .el-radio,` +
        `[data-field-code="${CSS.escape(code)}"] [role="radio"],` +
        `[field-code="${CSS.escape(code)}"] [role="radio"],` +
        `[prop="${CSS.escape(code)}"] [role="radio"]`;
    }
    if (code && !code.startsWith('el-id-') && kind === 'checkbox') {
      return `[data-field-code="${CSS.escape(code)}"] .el-checkbox-group,` +
        `[field-code="${CSS.escape(code)}"] .el-checkbox-group,` +
        `[prop="${CSS.escape(code)}"] .el-checkbox-group,` +
        `[data-field-code="${CSS.escape(code)}"] .el-checkbox,` +
        `[field-code="${CSS.escape(code)}"] .el-checkbox,` +
        `[prop="${CSS.escape(code)}"] .el-checkbox,` +
        `[data-field-code="${CSS.escape(code)}"] [role="checkbox"],` +
        `[field-code="${CSS.escape(code)}"] [role="checkbox"],` +
        `[prop="${CSS.escape(code)}"] [role="checkbox"]`;
    }
    if (code && !code.startsWith('el-id-')) {
      const escaped = CSS.escape(code);
      const collectionControl = `[data-ei-field-path="${escaped}"] input,` +
        `[data-ei-field-path="${escaped}"] textarea,` +
        `[data-ei-field-path="${escaped}"] select,` +
        `[data-ei-field-path="${escaped}"] [role="combobox"]`;
      if (el.closest('[data-ei-field-path]')) return collectionControl;
    }
    if (el.getAttribute('name')) return `[name="${CSS.escape(el.getAttribute('name'))}"]`;
    // The business code identifies the field, while the current element id
    // identifies the actual input to click/fill (the prop belongs to a wrapper).
    if (el.id) return `#${CSS.escape(el.id)}`;
    if (code && code.startsWith('el-id-')) return `#${CSS.escape(code)}`;
    if (code && !code.startsWith('el-id-')) return `[data-field-code="${CSS.escape(code)}"] input,[field-code="${CSS.escape(code)}"] input,[prop="${CSS.escape(code)}"] input,[prop="${CSS.escape(code)}"] textarea`;
    return '';
  };
  const kindOf = (el, label = '', code = '') => {
    const type = (el.getAttribute('type') || '').toLowerCase();
    const inputmode = (el.getAttribute('inputmode') || '').toLowerCase();
    const role = (el.getAttribute('role') || '').toLowerCase();
    const cls = `${el.className || ''} ${el.closest(componentControlSelector)?.className || ''}`.toLowerCase();
    if (type === 'file') return 'file';
    if (el.tagName === 'TEXTAREA') return 'textarea';
    // Element Plus date-picker inputs also expose role=combobox. Preserve the
    // component's stronger year-picker identity before the generic select rule.
    if (cls.includes('date-editor--year')) return 'year';
    // Component semantics are stronger than label heuristics. Element Plus
    // select inputs are readonly comboboxes and numeric column labels must not
    // turn them into number inputs.
    if (el.tagName === 'SELECT' || role === 'combobox' || cls.includes('select')) return cls.includes('multiple') ? 'multi_select' : 'select';
    if (type === 'radio' || role === 'radio' || cls.includes('radio')) return 'radio';
    if (type === 'checkbox' || role === 'checkbox' || role === 'switch' || cls.includes('checkbox') || cls.includes('switch')) return 'checkbox';
    if (type === 'number' || ['numeric', 'decimal'].includes(inputmode) ||
        cls.includes('input-number') || /(?:比例|百分比|金额|出资额|数量|回报率|收益率|人数|价格|单价|总价|额度|估值|面积|年限|期限|周期[（(]?(?:年|月|天)|天数|月数|次数|排序|序号)/.test(label) ||
        /(?:amount|rate|ratio|percent|percentage|quantity|count|price|totalprice|quota|valuation|area|duration|period(?:month|year|day)?|days?|months?|years?)$/i.test(code)) return 'number';
    if (type === 'date' || cls.includes('date')) return 'date';
    return 'text';
  };
  const seen = new Set();
  const seenOwners = new WeakSet();
  return controls.map((el) => {
    const owner = componentOwner(el);
    if (seenOwners.has(owner)) return null;
    seenOwners.add(owner);
    const label = labelOf(el);
    const code = codeOf(el, label);
    const kind = kindOf(el, label, code);
    const key = code || `${label}:${kind}`;
    if (!key || seen.has(key)) return null;
    seen.add(key);
    const placeholder = el.getAttribute('placeholder') || '';
    const column = el.closest('.el-col,.purvar_form_item,[class*="form-item"]');
    const row = column?.closest('.el-row,form,[role="dialog"]');
    const ratio = column && row && row.getBoundingClientRect().width > 0
      ? column.getBoundingClientRect().width / row.getBoundingClientRect().width : 0;
    const layoutProfile = ratio >= 0.75 ? 'full' : (ratio >= 0.3 && ratio <= 0.7 ? 'half' : '');
    return {field_code: code, label, kind, selector: selectorOf(el, code, label, kind), visible: true,
      required: el.required || el.getAttribute('aria-required') === 'true' || !!el.closest('.is-required'),
      readonly: disabledOf(el) ||
        (el.readOnly && !['select','multi_select','year','radio','checkbox'].includes(kind)),
      qcc_remote: placeholder.includes('企查查'),
      maxlength: el.maxLength >= 0 ? el.maxLength : null, minimum: el.getAttribute('min'),
      maximum: el.getAttribute('max'), step: el.getAttribute('step'),
      pattern: el.getAttribute('pattern') || '',
      layout_profile: layoutProfile};
  }).filter(Boolean);
}
"""


def scan_dom_fields(page: "Page", root=None) -> list[DomField]:
    items = root.evaluate(DOM_FIELD_SCRIPT) if root is not None else page.evaluate(
        DOM_FIELD_SCRIPT, None
    )
    fields = [DomField(**item) for item in items]
    for field in fields:
        if field.kind == "text" and is_semantic_numeric_field(
            field.field_code, field.label
        ):
            field.kind = "number"
    return fields
