import * as z from './vendor/zod/v4/index.js';

const navButtons = document.querySelectorAll('.nav-button');
const views = document.querySelectorAll('.view');
const message = document.getElementById('message');
const loading = document.getElementById('loading');
const rows = document.getElementById('application-rows');
const emptyTable = document.getElementById('empty-table');
const refreshTable = document.getElementById('refresh-table');
const applicationSearch = document.getElementById('application-search');
const statusFilter = document.getElementById('status-filter');
const matchDirection = document.getElementById('match-direction');
const matchPercent = document.getElementById('match-percent');
const checkedFrom = document.getElementById('checked-from');
const checkedTo = document.getElementById('checked-to');
const failedDirection = document.getElementById('failed-direction');
const failedCount = document.getElementById('failed-count');
const clearFilters = document.getElementById('clear-filters');
const filterSummary = document.getElementById('filter-summary');
const uploadForm = document.getElementById('upload-form');
const batchForm = document.getElementById('batch-form');
const uploadResultPanel = document.getElementById('upload-result-panel');
const batchResultPanel = document.getElementById('batch-result-panel');
const uploadModeButtons = document.querySelectorAll('[data-upload-mode]');
const manualFields = document.getElementById('manual-upload-fields');
const jsonFields = document.getElementById('json-fields');
const detailPanel = document.getElementById('detail-view');
const detailSubtitle = document.getElementById('detail-subtitle');
const detailContent = document.getElementById('detail-content');
const closeDetail = document.getElementById('close-detail');
const fullImageButton = document.getElementById('full-image-button');

const applicationFields = [
  ['brand_name', 'Brand Name'],
  ['class_type_designation', 'Class/Type Designation'],
  ['alcohol_content', 'Alcohol Content'],
  ['net_contents', 'Net Contents'],
  ['bottler_producer_name_address', 'Name and Address of Bottler/Producer'],
  ['country_of_origin', 'Country of Origin'],
  ['government_health_warning_statement', 'Government Health Warning Statement'],
];

const nonBlankString = z.string().trim().min(1, { message: 'required' });
const ApplicationIdSchema = z.string()
  .trim()
  .regex(/^APP-[0-9A-F]{8}$/, { message: 'must look like APP-1B81036D' });
const ApplicationDataSchema = z.object({
  brand_name: nonBlankString,
  class_type_designation: nonBlankString,
  alcohol_content: nonBlankString,
  net_contents: nonBlankString,
  bottler_producer_name_address: nonBlankString,
  country_of_origin: nonBlankString,
  government_health_warning_statement: nonBlankString,
}).strict();

const ApplicationPackageSchema = z.object({
  application_id: ApplicationIdSchema.optional(),
  image_filename: nonBlankString,
  application_data: ApplicationDataSchema,
}).strict();

const BatchApplicationPackageSchema = z.array(ApplicationPackageSchema)
  .min(1, { message: 'Batch JSON must contain at least one application.' })
  .max(5, { message: 'Batch JSON can contain 5 applications or fewer.' });

const fieldLabels = {
  brand: 'Brand Name',
  product_class: 'Class/Type Designation',
  abv: 'Alcohol Content',
  net_contents: 'Net Contents',
  producer: 'Name and Address of Bottler/Producer',
  country: 'Country of Origin',
  government_warning: 'Government Health Warning Statement',
};

let applications = [];
let uploadMode = 'manual';
let pendingPollTimer = null;
let currentDetailId = null;
let currentImageUrl = '';
let activeUploadResultIds = [];
let activeUploadResultPanel = null;

function escapeHtml(value) {
  return String(value ?? '')
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#039;');
}

function formatZodIssues(error) {
  return error.issues.slice(0, 4).map((issue) => {
    const path = issue.path.length ? issue.path.join('.') : 'JSON';
    return `${path}: ${issue.message}`;
  }).join(' ');
}

async function validateJsonFile(file, schema, expectedShape) {
  let parsed = null;
  try {
    parsed = JSON.parse(await file.text());
  } catch (error) {
    return {
      ok: false,
      message: `${expectedShape} is not valid JSON.`,
    };
  }

  const result = schema.safeParse(parsed);
  if (!result.success) {
    return {
      ok: false,
      message: `${expectedShape} has the wrong shape. ${formatZodIssues(result.error)}`,
    };
  }

  return {
    ok: true,
    data: result.data,
  };
}

function showView(viewId) {
  views.forEach((view) => {
    const active = view.id === viewId;
    view.classList.toggle('active', active);
    view.hidden = !active;
  });
  navButtons.forEach((button) => {
    button.classList.toggle('active', button.dataset.view === viewId);
  });
  detailPanel.hidden = true;
  clearMessage();
}

function setUploadMode(mode) {
  uploadMode = mode;
  const isJson = mode === 'json';
  manualFields.hidden = isJson;
  jsonFields.hidden = !isJson;
  uploadModeButtons.forEach((button) => {
    const active = button.dataset.uploadMode === mode;
    button.classList.toggle('active', active);
    button.setAttribute('aria-selected', String(active));
  });
  clearMessage();
}

function clearMessage() {
  message.textContent = '';
  message.classList.remove('show');
  hideInlineResults();
  document.querySelectorAll('[aria-invalid="true"]').forEach((control) => {
    control.removeAttribute('aria-invalid');
  });
}

function showMessage(text, controls = []) {
  message.textContent = text;
  message.classList.add('show');
  controls.forEach((control) => control.setAttribute('aria-invalid', 'true'));
  message.focus({ preventScroll: false });
}

function showInfo(text) {
  message.textContent = text;
  message.classList.add('show');
  message.focus({ preventScroll: false });
}

function hideInlineResults() {
  [uploadResultPanel, batchResultPanel].forEach((panel) => {
    panel.hidden = true;
    panel.innerHTML = '';
  });
}

function setBusy(isBusy, text = 'Matching application now.') {
  loading.textContent = text;
  loading.classList.toggle('show', isBusy);
  document.querySelectorAll('input, textarea, button').forEach((control) => {
    control.disabled = isBusy;
  });
}

function statusLabel(status) {
  if (status === 'ACCEPTED') return 'Accepted';
  if (status === 'NEEDS_CHECK') return 'Needs Check';
  if (status === 'REJECTED') return 'Rejected';
  if (status === 'ERROR') return 'Error';
  return 'Pending';
}

function failedFieldCount(record) {
  const results = record.verification_result?.field_results || [];
  return results.filter((result) => result.status === 'FAIL').length;
}

function matchPercentage(record) {
  if (typeof record.match_percentage === 'number') {
    return record.match_percentage;
  }
  const results = record.verification_result?.field_results || [];
  if (!results.length) return null;
  const passed = results.filter((result) => result.status === 'PASS').length;
  return Math.round((passed / results.length) * 100);
}

function percentageClass(record) {
  const percentage = matchPercentage(record);
  if (percentage === null) return 'pending';
  if (percentage === 100) return 'accepted';
  if (percentage >= 50) return 'warning';
  return 'rejected';
}

function renderMatchPercentage(record) {
  const percentage = matchPercentage(record);
  if (percentage === null) {
    return '<span class="score-pill pending">Not matched</span>';
  }
  return `<span class="score-pill ${percentageClass(record)}">${percentage}%</span>`;
}

function formatChecked(value) {
  if (!value) return 'Not checked';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString();
}

function normalizeSearchText(value) {
  return String(value ?? '').trim().toLowerCase();
}

function searchableRecordText(record) {
  const data = record.application_data || {};
  const resultFields = record.verification_result?.field_results || [];
  const statusText = statusLabel(record.status);
  const statusAliases = {
    NEEDS_CHECK: 'needs review needs check review failed fields',
    ACCEPTED: 'accepted approved pass',
    REJECTED: 'rejected reject failed',
    ERROR: 'error upload failed',
    PENDING: 'pending not matched processing',
  };
  const fieldText = resultFields.map((result) => [
    fieldLabels[result.field] || result.field,
    result.expected,
    result.found,
    result.status,
    result.message,
  ].join(' ')).join(' ');

  return normalizeSearchText([
    record.application_id,
    record.image_filename,
    record.status,
    statusText,
    statusAliases[record.status],
    formatChecked(record.checked_at),
    ...applicationFields.map(([key]) => data[key]),
    fieldText,
  ].join(' '));
}

function dateOnlyValue(value, endOfDay = false) {
  if (!value) return null;
  const date = new Date(`${value}T00:00:00`);
  if (Number.isNaN(date.getTime())) return null;
  if (endOfDay) {
    date.setDate(date.getDate() + 1);
    date.setMilliseconds(date.getMilliseconds() - 1);
  }
  return date;
}

function optionalNumber(value) {
  if (String(value ?? '').trim() === '') return Number.NaN;
  return Number(value);
}

function compareNumber(value, direction, target) {
  if (!direction || Number.isNaN(target)) return true;
  if (typeof value !== 'number') return false;
  if (direction === 'gte') return value >= target;
  if (direction === 'lte') return value <= target;
  if (direction === 'eq') return value === target;
  return true;
}

function getFilteredApplications() {
  const search = normalizeSearchText(applicationSearch.value);
  const status = statusFilter.value;
  const matchTarget = optionalNumber(matchPercent.value);
  const failedTarget = optionalNumber(failedCount.value);
  const fromDate = dateOnlyValue(checkedFrom.value);
  const toDate = dateOnlyValue(checkedTo.value, true);

  return applications.filter((record) => {
    if (search && !searchableRecordText(record).includes(search)) {
      return false;
    }
    if (status && record.status !== status) {
      return false;
    }
    if (!compareNumber(matchPercentage(record), matchDirection.value, matchTarget)) {
      return false;
    }
    if (!compareNumber(failedFieldCount(record), failedDirection.value, failedTarget)) {
      return false;
    }
    if (fromDate || toDate) {
      if (!record.checked_at) return false;
      const checked = new Date(record.checked_at);
      if (Number.isNaN(checked.getTime())) return false;
      if (fromDate && checked < fromDate) return false;
      if (toDate && checked > toDate) return false;
    }
    return true;
  });
}

function renderTable() {
  const visibleApplications = getFilteredApplications();
  rows.innerHTML = visibleApplications.map((record) => `
    <tr data-application-id="${escapeHtml(record.application_id)}" tabindex="0">
      <td>${escapeHtml(record.application_id)}</td>
      <td>${escapeHtml(record.application_data?.brand_name || '')}</td>
      <td>${escapeHtml(record.image_filename)}</td>
      <td><span class="status ${escapeHtml(record.status.toLowerCase())}">${statusLabel(record.status)}</span></td>
      <td>${renderMatchPercentage(record)}</td>
      <td>${failedFieldCount(record)}</td>
      <td>${escapeHtml(formatChecked(record.checked_at))}</td>
    </tr>
  `).join('');
  emptyTable.hidden = visibleApplications.length > 0;
  emptyTable.textContent = applications.length ? 'No applications match these filters.' : 'No applications matched yet.';
  filterSummary.textContent = `${visibleApplications.length} of ${applications.length} application${applications.length === 1 ? '' : 's'} shown.`;
}

function failedFieldSummaries(record) {
  const results = record.verification_result?.field_results || [];
  return results
    .filter((result) => result.status === 'FAIL')
    .map((result) => {
      const label = fieldLabels[result.field] || result.field.replaceAll('_', ' ');
      const expected = result.expected || 'Blank';
      const found = result.found || 'Not found';
      return `${label} expected "${expected}" but found "${found}"`;
    });
}

function uploadOutcomeSummary(records) {
  const problems = records
    .filter((record) => ['ERROR', 'NEEDS_CHECK', 'REJECTED'].includes(record.status))
    .map((record) => {
      if (record.status === 'ERROR') {
        return `${record.application_id}: Error - ${record.error || 'The application could not be processed.'}`;
      }
      const fieldReasons = failedFieldSummaries(record);
      const reason = fieldReasons.length ? fieldReasons.join('; ') : 'One or more values did not match.';
      return `${record.application_id}: ${statusLabel(record.status)} - ${reason}`;
    });

  if (problems.length) {
    return problems.join(' ');
  }

  if (records.some((record) => record.status === 'PENDING')) {
    return `${records.length} application${records.length === 1 ? '' : 's'} added as pending. Matching is processing now.`;
  }

  return `${records.length} application${records.length === 1 ? '' : 's'} accepted.`;
}

function hasImmediateProblem(records) {
  return records.some((record) => record.status === 'ERROR');
}

function renderInlineUploadResults(panel, records) {
  panel.innerHTML = `
    <h3>Upload Result</h3>
    <div class="inline-result-list">
      ${records.map((record) => {
        const failedFields = failedFieldSummaries(record);
        const problemLines = record.status === 'ERROR'
          ? [record.error || 'The application could not be processed.']
          : failedFields;
        return `
          <article class="inline-result ${percentageClass(record)}">
            <div class="match-head">
              <h4>${escapeHtml(record.application_id)}</h4>
              <span class="status ${escapeHtml(record.status.toLowerCase())}">${statusLabel(record.status)}</span>
            </div>
            <p><strong>Image:</strong> ${escapeHtml(record.image_filename)}</p>
            <p><strong>Match:</strong> ${matchPercentage(record) === null ? 'Not matched yet' : `${matchPercentage(record)}%`}</p>
            ${problemLines.length ? `<ul>${problemLines.map((line) => `<li>${escapeHtml(line)}</li>`).join('')}</ul>` : '<p>No upload errors found.</p>'}
          </article>
        `;
      }).join('')}
    </div>
  `;
  panel.hidden = false;
}

async function loadApplications() {
  clearMessage();
  try {
    const response = await fetch('/applications');
    if (!response.ok) throw new Error('Could not load applications.');
    applications = await response.json();
    renderTable();
    if (currentDetailId) {
      openDetail(currentDetailId, { scroll: false });
    }
  } catch (error) {
    showMessage('The application table could not be loaded. Please try again.');
  }
}

function startPendingPoll(panel = null, trackedIds = []) {
  if (pendingPollTimer) {
    clearInterval(pendingPollTimer);
  }
  activeUploadResultPanel = panel;
  activeUploadResultIds = trackedIds;
  pendingPollTimer = setInterval(async () => {
    const pendingIds = applications
      .filter((record) => record.status === 'PENDING')
      .map((record) => record.application_id);
    if (!pendingIds.length) {
      clearInterval(pendingPollTimer);
      pendingPollTimer = null;
      return;
    }
    await loadApplications();
    const completedRecords = applications.filter((record) => (
      pendingIds.includes(record.application_id) && record.status !== 'PENDING'
    ));
    if (completedRecords.length) {
      showInfo(uploadOutcomeSummary(completedRecords));
      if (activeUploadResultPanel && activeUploadResultIds.length) {
        const trackedRecords = applications.filter((record) => activeUploadResultIds.includes(record.application_id));
        renderInlineUploadResults(activeUploadResultPanel, trackedRecords);
      }
    }
  }, 1500);
}

async function updateApplicationStatus(applicationId, nextStatus) {
  clearMessage();
  try {
    const response = await fetch(`/applications/${encodeURIComponent(applicationId)}/status`, {
      method: 'PATCH',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({ status: nextStatus }),
    });
    if (!response.ok) {
      showMessage(await readProblem(response));
      return;
    }
    const record = await response.json();
    applications = applications.map((item) => (
      item.application_id === record.application_id ? record : item
    ));
    renderTable();
    openDetail(record.application_id, { scroll: false });
  } catch (error) {
    showMessage('The application status could not be changed. Please try again.');
  }
}

function buildApplicationDataFromForm() {
  const data = {};
  applicationFields.forEach(([key]) => {
    data[key] = uploadForm.elements[key].value.trim();
  });
  return data;
}

function missingUploadControls() {
  const controls = [];
  if (uploadMode === 'json') {
    if (!uploadForm.elements.application_json_file.files[0]) {
      controls.push(uploadForm.elements.application_json_file);
    }
    return controls;
  }

  if (!uploadForm.elements.image.files[0]) controls.push(uploadForm.elements.image);
  applicationFields.forEach(([key]) => {
    const control = uploadForm.elements[key];
    if (!control.value.trim()) controls.push(control);
  });
  return controls;
}

async function readProblem(response) {
  const problem = await readProblemDetails(response);
  return problem.message;
}

async function readProblemDetails(response) {
  try {
    const data = await response.json();
    const detail = data.detail;
    if (typeof detail === 'string') {
      return { message: detail, uploadFailures: [] };
    }
    if (detail?.message || Array.isArray(detail?.upload_failures)) {
      return {
        message: detail.message || 'The request could not be completed.',
        uploadFailures: Array.isArray(detail.upload_failures) ? detail.upload_failures : [],
      };
    }
  } catch (error) {
    return { message: '', uploadFailures: [] };
  }
  return { message: 'The request could not be completed.', uploadFailures: [] };
}

function renderInlineUploadFailures(panel, failures) {
  panel.innerHTML = `
    <h3>Upload Failed</h3>
    <p class="upload-failure-summary">
      ${failures.length === 1
        ? '1 application failed verification and was not added to the table.'
        : `${failures.length} applications failed verification. None of them were added to the table.`}
    </p>
    <div class="inline-result-list">
      ${failures.map((failure) => {
        const failedFields = Array.isArray(failure.failed_fields) ? failure.failed_fields : [];
        const status = failure.status || 'ERROR';
        const match = typeof failure.match_percentage === 'number' ? `${failure.match_percentage}%` : 'Not matched';
        const reason = failure.error || 'The application could not be processed.';
        const fieldCount = failedFields.length;
        return `
          <article class="inline-result rejected upload-failure-card">
            <div class="match-head">
              <div>
                <p class="failure-kicker">Application Failed</p>
                <h4>${escapeHtml(failure.application_id || 'Application')}</h4>
              </div>
              <span class="status rejected">Not Uploaded</span>
            </div>
            <div class="failure-facts">
              <div>
                <span>Image</span>
                <strong>${escapeHtml(failure.image_filename || 'Not provided')}</strong>
              </div>
              <div>
                <span>Verification Status</span>
                <strong>${escapeHtml(statusLabel(status))}</strong>
              </div>
              <div>
                <span>Match</span>
                <strong>${escapeHtml(match)}</strong>
              </div>
              <div>
                <span>Failed Fields</span>
                <strong>${fieldCount || 'No field details'}</strong>
              </div>
            </div>
            <div class="failure-reason">
              <strong>Why upload failed</strong>
              <p>${escapeHtml(reason)}</p>
            </div>
            ${fieldCount ? `
              <div class="failed-field-list" aria-label="Failed fields for ${escapeHtml(failure.application_id || 'application')}">
                ${failedFields.map((field, index) => {
                  const label = field.label || field.field || 'Field';
                  const expected = field.expected || 'Blank';
                  const found = field.found || 'Not found';
                  const message = field.message || 'This value did not match the application data.';
                  return `
                    <section class="failed-field">
                      <div class="failed-field-head">
                        <span>Failed Field ${index + 1}</span>
                        <strong>${escapeHtml(label)}</strong>
                      </div>
                      <dl>
                        <div>
                          <dt>Expected from application</dt>
                          <dd>${escapeHtml(expected)}</dd>
                        </div>
                        <div>
                          <dt>Found on label</dt>
                          <dd>${escapeHtml(found)}</dd>
                        </div>
                        <div>
                          <dt>Reason</dt>
                          <dd>${escapeHtml(message)}</dd>
                        </div>
                      </dl>
                    </section>
                  `;
                }).join('')}
              </div>
            ` : `
              <p class="failure-empty-detail">No field-level comparison was available. Check the image filename and upload format first.</p>
            `}
          </article>
        `;
      }).join('')}
    </div>
  `;
  panel.hidden = false;
  panel.setAttribute('tabindex', '-1');
  panel.focus({ preventScroll: true });
}

function showUploadProblem(panel, problem) {
  showMessage(problem.message || 'Upload failed. The application was not added to the table.');
  if (problem.uploadFailures.length) {
    renderInlineUploadFailures(panel, problem.uploadFailures);
  }
}

async function submitApplication(event) {
  event.preventDefault();
  clearMessage();
  const missing = missingUploadControls();
  if (missing.length) {
    showMessage('Please complete the application data and choose a label image.', missing);
    return;
  }

  const formData = new FormData();
  const url = uploadMode === 'json' ? '/applications/upload-json' : '/applications/upload';

  if (uploadMode === 'json') {
    const file = uploadForm.elements.application_json_file.files[0];
    const validation = await validateJsonFile(file, ApplicationPackageSchema, 'Application JSON');
    if (!validation.ok) {
      showMessage(validation.message, [uploadForm.elements.application_json_file]);
      return;
    }
    formData.append(
      'application_file',
      new Blob([JSON.stringify(validation.data)], { type: 'application/json' }),
      file.name,
    );
  } else {
    const image = uploadForm.elements.image.files[0];
    const packageData = {
      image_filename: image.name,
      application_data: buildApplicationDataFromForm(),
    };
    formData.append('image', image);
    formData.append('application', JSON.stringify(packageData));
  }

  setBusy(true);
  try {
    const response = await fetch(url, {
      method: 'POST',
      body: formData,
    });
    if (!response.ok) {
      showUploadProblem(uploadResultPanel, await readProblemDetails(response));
      return;
    }
    const record = await response.json();
    applications = applications.filter((item) => item.application_id !== record.application_id);
    applications.unshift(record);
    renderTable();
    if (hasImmediateProblem([record])) {
      renderInlineUploadResults(uploadResultPanel, [record]);
      showInfo(uploadOutcomeSummary([record]));
      startPendingPoll(uploadResultPanel, [record.application_id]);
      return;
    }
    uploadForm.reset();
    showView('table-view');
    openDetail(record.application_id);
    showInfo(uploadOutcomeSummary([record]));
    startPendingPoll(null, [record.application_id]);
  } catch (error) {
    showMessage('The matching service could not be reached. Please try again.');
  } finally {
    setBusy(false);
  }
}

async function submitBatch(event) {
  event.preventDefault();
  clearMessage();
  const file = batchForm.elements.application_file.files[0];
  if (!file) {
    showMessage('Please choose a JSON application file.', [batchForm.elements.application_file]);
    return;
  }

  const formData = new FormData();
  const validation = await validateJsonFile(file, BatchApplicationPackageSchema, 'Batch JSON');
  if (!validation.ok) {
    showMessage(validation.message, [batchForm.elements.application_file]);
    return;
  }
  formData.append(
    'application_file',
    new Blob([JSON.stringify(validation.data)], { type: 'application/json' }),
    file.name,
  );

  setBusy(true, 'Matching batch now.');
  try {
    const response = await fetch('/applications/batch', {
      method: 'POST',
      body: formData,
    });
    if (!response.ok) {
      showUploadProblem(batchResultPanel, await readProblemDetails(response));
      return;
    }
    const records = await response.json();
    const incomingIds = new Set(records.map((record) => record.application_id));
    applications = [
      ...records,
      ...applications.filter((record) => !incomingIds.has(record.application_id)),
    ];
    renderTable();
    if (hasImmediateProblem(records)) {
      renderInlineUploadResults(batchResultPanel, records);
      showInfo(uploadOutcomeSummary(records));
      startPendingPoll(batchResultPanel, records.map((record) => record.application_id));
      return;
    }
    batchForm.reset();
    showView('table-view');
    showInfo(uploadOutcomeSummary(records));
    startPendingPoll(null, records.map((record) => record.application_id));
  } catch (error) {
    showMessage('The batch could not be uploaded. Please try again.');
  } finally {
    setBusy(false);
  }
}

function renderApplicationData(data) {
  return applicationFields.map(([key, label]) => `
    <div class="data-row">
      <dt>${escapeHtml(label)}</dt>
      <dd>${escapeHtml(data?.[key] || '')}</dd>
    </div>
  `).join('');
}

function renderFieldResults(record) {
  const results = record.verification_result?.field_results || [];
  if (!results.length) {
    return `<p class="empty">${escapeHtml(record.error || 'This application has no match result yet.')}</p>`;
  }

  return results.map((result) => {
    const label = fieldLabels[result.field] || result.field.replaceAll('_', ' ');
    const statusClass = result.status === 'PASS' ? 'accepted' : 'rejected';
    return `
      <article class="match-row ${statusClass}">
        <div class="match-head">
          <h4>${escapeHtml(label)}</h4>
          <span class="status ${statusClass}">${escapeHtml(result.status)}</span>
        </div>
        <p><strong>Expected:</strong> ${escapeHtml(result.expected || 'Blank')}</p>
        <p><strong>Extracted:</strong> ${escapeHtml(result.found || 'Not found')}</p>
        <p>${escapeHtml(result.message || '')}</p>
      </article>
    `;
  }).join('');
}

function openDetail(applicationId, options = {}) {
  const record = applications.find((item) => item.application_id === applicationId);
  if (!record) return;

  currentDetailId = applicationId;
  currentImageUrl = `/applications/images/${encodeURIComponent(record.image_filename)}`;
  detailSubtitle.textContent = `${record.application_id} - ${statusLabel(record.status)}`;
  detailContent.innerHTML = `
    <div class="detail-grid">
      <section>
        <h3>Submitted Application Data</h3>
        <dl class="data-list">${renderApplicationData(record.application_data)}</dl>
      </section>
      <section>
        <h3>Match Result</h3>
        <div class="result-banner ${record.status.toLowerCase()}">
          ${statusLabel(record.status)}
        </div>
        <div class="detail-score ${percentageClass(record)}">
          ${matchPercentage(record) === null ? 'Not matched yet' : `${matchPercentage(record)}% matched`}
        </div>
        <img class="label-preview" src="${currentImageUrl}" alt="Label image for ${escapeHtml(record.application_id)}" />
        <p><strong>Image:</strong> ${escapeHtml(record.image_filename)}</p>
        <p><strong>Checked:</strong> ${escapeHtml(formatChecked(record.checked_at))}</p>
        ${record.error ? `<p class="error-text">${escapeHtml(record.error)}</p>` : ''}
        <div class="status-actions" aria-label="Change application status">
          <button class="secondary-action status-change" type="button" data-status="ACCEPTED">Mark Accepted</button>
          <button class="secondary-action status-change danger" type="button" data-status="REJECTED">Mark Rejected</button>
        </div>
      </section>
    </div>
    <section class="field-results">
      <h3>Field Matching</h3>
      ${renderFieldResults(record)}
    </section>
  `;
  detailPanel.hidden = false;
  if (options.scroll !== false) {
    detailPanel.scrollIntoView({ behavior: 'smooth', block: 'start' });
  }
}

navButtons.forEach((button) => {
  button.addEventListener('click', () => showView(button.dataset.view));
});

uploadModeButtons.forEach((button) => {
  button.addEventListener('click', () => setUploadMode(button.dataset.uploadMode));
});

[
  applicationSearch,
  statusFilter,
  matchDirection,
  matchPercent,
  checkedFrom,
  checkedTo,
  failedDirection,
  failedCount,
].forEach((control) => {
  control.addEventListener('input', renderTable);
  control.addEventListener('change', renderTable);
});

clearFilters.addEventListener('click', () => {
  applicationSearch.value = '';
  statusFilter.value = '';
  matchDirection.value = '';
  matchPercent.value = '';
  checkedFrom.value = '';
  checkedTo.value = '';
  failedDirection.value = '';
  failedCount.value = '';
  renderTable();
});

refreshTable.addEventListener('click', loadApplications);
uploadForm.addEventListener('submit', submitApplication);
batchForm.addEventListener('submit', submitBatch);
closeDetail.addEventListener('click', () => {
  detailPanel.hidden = true;
  currentDetailId = null;
  currentImageUrl = '';
});

fullImageButton.addEventListener('click', () => {
  if (!currentImageUrl) return;
  window.open(currentImageUrl, '_blank', 'noopener,noreferrer');
});

detailContent.addEventListener('click', (event) => {
  const button = event.target.closest('.status-change');
  if (!button || !currentDetailId) return;
  updateApplicationStatus(currentDetailId, button.dataset.status);
});

rows.addEventListener('click', (event) => {
  const row = event.target.closest('tr[data-application-id]');
  if (row) openDetail(row.dataset.applicationId);
});

rows.addEventListener('keydown', (event) => {
  if (event.key !== 'Enter' && event.key !== ' ') return;
  const row = event.target.closest('tr[data-application-id]');
  if (row) {
    event.preventDefault();
    openDetail(row.dataset.applicationId);
  }
});

loadApplications();
