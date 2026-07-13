import { createBatchController } from './batch-ui.js';

const singleMode = document.getElementById('single-mode');
    const batchMode = document.getElementById('batch-mode');
    const singlePanel = document.getElementById('single-panel');
    const batchPanel = document.getElementById('batch-panel');
    const singleForm = document.getElementById('single-form');
    const batchForm = document.getElementById('batch-form');
    const imageInput = document.getElementById('image');
    const chosenFile = document.getElementById('chosen-file');
    const singlePreview = document.getElementById('single-preview');
    const singlePreviewImage = document.getElementById('single-preview-image');
    const singleSubmit = document.getElementById('single-submit');
    const batchSubmit = document.getElementById('batch-submit');
    const addLabelButton = document.getElementById('add-label');
    const batchList = document.getElementById('batch-list');
    const errorMessage = document.getElementById('error-message');
    const loadingMessage = document.getElementById('loading-message');
    const results = document.getElementById('results');
    const verdict = document.getElementById('verdict');
    const summary = document.getElementById('summary');
    const resultList = document.getElementById('result-list');
    const time = document.getElementById('time');

    const maxBatchLabels = 5;
    let progressTimer = null;

    const singleFields = [
      { key: 'brand', element: document.getElementById('brand') },
      { key: 'class', element: document.getElementById('product-class') },
      { key: 'producer', element: document.getElementById('producer') },
      { key: 'country', element: document.getElementById('country') },
      { key: 'abv', element: document.getElementById('abv') },
      { key: 'net_contents', element: document.getElementById('net-contents') },
      { key: 'government_warning', element: document.getElementById('government-warning') },
    ];

    const fieldLabels = {
      brand: 'Brand',
      product_class: 'Class or Type',
      producer: 'Producer',
      country: 'Country',
      abv: 'Alcohol %',
      net_contents: 'Bottle Size',
      government_warning: 'Government Warning',
    };

    const orderedFieldNames = [
      'brand',
      'product_class',
      'producer',
      'country',
      'abv',
      'net_contents',
      'government_warning',
    ];

    function normalizeFormFieldName(fieldName) {
      return fieldName === 'product_class' ? 'class' : fieldName;
    }

    function labelForField(fieldName) {
      const normalized = normalizeFormFieldName(fieldName);
      if (normalized === 'class') {
        return fieldLabels.product_class;
      }
      return fieldLabels[fieldName] || fieldLabels[normalized] || normalized.replaceAll('_', ' ');
    }

    function uniqueValues(values) {
      return [...new Set(values.filter(Boolean))];
    }

    function escapeHtml(value) {
      return String(value ?? '')
        .replaceAll('&', '&amp;')
        .replaceAll('<', '&lt;')
        .replaceAll('>', '&gt;')
        .replaceAll('"', '&quot;')
        .replaceAll("'", '&#039;');
    }

    function clearError() {
      errorMessage.textContent = '';
      errorMessage.classList.remove('show');
      document.querySelectorAll('[aria-invalid="true"]').forEach((control) => {
        control.removeAttribute('aria-invalid');
      });
    }

    function markInvalidControls(invalidControls = []) {
      invalidControls.forEach((control) => {
        if (control) {
          control.setAttribute('aria-invalid', 'true');
        }
      });
    }

    function focusAfterPaint(element) {
      window.requestAnimationFrame(() => {
        element.focus({ preventScroll: false });
      });
    }

    function showError(message, invalidControls = []) {
      errorMessage.textContent = message;
      errorMessage.classList.add('show');
      markInvalidControls(invalidControls);
      focusAfterPaint(errorMessage);
    }

    function showReviewMessage(message, invalidControls = []) {
      errorMessage.textContent = message;
      errorMessage.classList.add('show');
      markInvalidControls(invalidControls);
    }

    function clearResults() {
      results.classList.remove('show');
      verdict.className = '';
      verdict.innerHTML = '';
      summary.innerHTML = '';
      resultList.innerHTML = '';
      time.textContent = '';
    }

    function renderImagePreview(fileInput, fileLabel, preview, previewImage) {
      const previousObjectUrl = preview.dataset.objectUrl;
      if (previousObjectUrl) {
        URL.revokeObjectURL(previousObjectUrl);
        delete preview.dataset.objectUrl;
      }

      const file = fileInput.files && fileInput.files[0];
      fileLabel.textContent = file ? `Chosen photo: ${file.name}` : 'No photo chosen yet.';
      preview.hidden = !file;
      previewImage.removeAttribute('src');

      if (!file) {
        return;
      }

      const objectUrl = URL.createObjectURL(file);
      preview.dataset.objectUrl = objectUrl;
      previewImage.src = objectUrl;
    }

    const batchController = createBatchController({
      batchList,
      addLabelButton,
      maxBatchLabels,
      clearError,
      renderImagePreview,
    });

    function setMode(mode) {
      const isBatch = mode === 'batch';
      batchPanel.classList.toggle('hidden', !isBatch);
      singlePanel.classList.toggle('hidden', isBatch);
      batchPanel.hidden = !isBatch;
      singlePanel.hidden = isBatch;
      batchMode.classList.toggle('active', isBatch);
      singleMode.classList.toggle('active', !isBatch);
      batchMode.setAttribute('aria-selected', String(isBatch));
      singleMode.setAttribute('aria-selected', String(!isBatch));
      batchMode.tabIndex = isBatch ? 0 : -1;
      singleMode.tabIndex = isBatch ? -1 : 0;
      clearError();
      clearResults();
    }

    function setBusy(isBusy, labelCount = 1) {
      const controls = document.querySelectorAll('input, textarea, button');
      controls.forEach((control) => {
        control.disabled = isBusy;
      });

      if (!isBusy) {
        batchController.updateButtons();
      }

      singleSubmit.textContent = isBusy ? 'Checking...' : 'Check Label';
      batchSubmit.textContent = isBusy ? 'Checking...' : 'Check All Labels';
      loadingMessage.textContent = 'Checking labels now.';
      loadingMessage.classList.toggle('show', isBusy);

      if (progressTimer) {
        clearTimeout(progressTimer);
        progressTimer = null;
      }

      if (isBusy) {
        progressTimer = setTimeout(() => {
          loadingMessage.textContent = labelCount === 1
            ? 'Still checking the label...'
            : `Checking ${labelCount} labels...`;
        }, 800);
      }
    }

    function readProblem(responseBody, statusCode) {
      const detailBody = responseBody && responseBody.detail ? responseBody.detail : '';
      const fieldErrors = Array.isArray(detailBody.field_errors) ? detailBody.field_errors : [];
      const detail = typeof detailBody === 'object'
        ? String(detailBody.message || '')
        : String(detailBody || '');
      const lowerDetail = detail.toLowerCase();

      if (fieldErrors.length) {
        return {
          message: detail || 'Please fix the highlighted fields.',
          fieldErrors,
        };
      }

      if (lowerDetail.includes('invalid image format')) {
        return { message: 'Please choose an image file.', fieldErrors: [] };
      }

      if (lowerDetail.includes('too large') || lowerDetail.includes('maximum allowed size is 5')) {
        return {
          message: lowerDetail.includes('batch')
          ? 'Please check 5 labels or fewer at one time.'
          : 'Please choose an image under 5 MB.',
          fieldErrors: [],
        };
      }

      if (lowerDetail.includes('each label needs')) {
        return { message: 'Each label needs one photo and one set of boxes.', fieldErrors: [] };
      }

      if (lowerDetail.includes('application_data') || lowerDetail.includes('missing required fields')) {
        return { message: 'Please fill in all seven boxes for each label.', fieldErrors: [] };
      }

      if (statusCode >= 500) {
        return { message: 'The label checker had a problem. Please try again.', fieldErrors: [] };
      }

      return {
        message: detail || 'Something went wrong while checking the label. Please try again.',
        fieldErrors: [],
      };
    }

    function plainReason(result) {
      const message = String(result.message || '').toLowerCase();

      if (message.includes('missing') || !result.found) {
        return 'The label checker could not find this on the label.';
      }

      if (result.field === 'government_warning') {
        return 'The government warning must match exactly, including capital letters, punctuation, and spacing.';
      }

      return 'The value found on the label does not match the expected value.';
    }

    function buildSinglePayload() {
      const data = {};
      singleFields.forEach((field) => {
        data[field.key] = field.element.value.trim();
      });
      return data;
    }

    function singleHasAllFields() {
      return singleFields.every((field) => field.element.value.trim().length > 0);
    }

    function missingSingleControls() {
      const missing = [];
      if (!imageInput.files || !imageInput.files[0]) {
        missing.push(imageInput);
      }
      singleFields.forEach((field) => {
        if (!field.element.value.trim()) {
          missing.push(field.element);
        }
      });
      return missing;
    }

    function singleControlsForFieldErrors(fieldErrors = []) {
      return fieldErrors.map((error) => {
        const field = normalizeFormFieldName(error.field);
        const match = singleFields.find((item) => item.key === field);
        return match ? match.element : null;
      }).filter(Boolean);
    }

    function failedFieldNames(fieldResults = []) {
      return fieldResults
        .filter((result) => result.status === 'FAIL')
        .map((result) => result.field);
    }

    function failedFieldReviewMessage(fieldNames) {
      const labels = uniqueValues(fieldNames.map(labelForField));
      return labels.length
        ? `Please review: ${labels.join(', ')}.`
        : 'Please review the highlighted fields.';
    }

    function renderFieldResults(fieldResults) {
      const byName = new Map((fieldResults || []).map((result) => [result.field, result]));
      return orderedFieldNames.map((fieldName) => {
        const result = byName.get(fieldName);
        const label = fieldLabels[fieldName] || fieldName;

        if (!result) {
          return `
            <article class="result-row fail">
              <div class="result-head">
                <div class="result-name">${escapeHtml(label)}</div>
                <div class="badge fail">FAIL</div>
              </div>
              <p class="reason">This result was not returned. Please try again.</p>
            </article>
          `;
        }

        const didPass = result.status === 'PASS';
        const expected = escapeHtml(result.expected || '');
        const found = escapeHtml(result.found || 'Not found');
        const detail = result.message ? `<p class="detail">${escapeHtml(result.message)}</p>` : '';

        if (didPass) {
          return `
            <article class="result-row pass">
              <div class="result-head">
                <div class="result-name">${escapeHtml(label)}</div>
                <div class="badge pass">PASS</div>
              </div>
              <p class="value-line"><span class="value-label">Found on Label:</span> ${found}</p>
            </article>
          `;
        }

        return `
          <article class="result-row fail">
            <div class="result-head">
              <div class="result-name">${escapeHtml(label)}</div>
              <div class="badge fail">FAIL</div>
            </div>
            <p class="reason">${escapeHtml(plainReason(result))}</p>
            <p class="value-line"><span class="value-label">Expected:</span> ${expected || 'Blank'}</p>
            <p class="value-line"><span class="value-label">Found on Label:</span> ${found}</p>
            ${detail}
          </article>
        `;
      }).join('');
    }

    function renderSingleResults(data) {
      const isApproved = data.overall_verdict === 'APPROVED';
      const failedFields = failedFieldNames(data.field_results || []);
      verdict.className = `verdict ${isApproved ? 'approved' : 'review'}`;
      verdict.innerHTML = `
        <span class="verdict-title">${isApproved ? 'APPROVED' : 'NEEDS REVIEW'}</span>
        <p class="verdict-copy">${isApproved ? 'All fields matched.' : 'One or more fields need a closer look.'}</p>
      `;
      summary.innerHTML = '';
      resultList.innerHTML = renderFieldResults(data.field_results);
      time.textContent = typeof data.latency_ms === 'number'
        ? `Checked in ${(data.latency_ms / 1000).toFixed(1)} seconds.`
        : '';
      if (failedFields.length) {
        showReviewMessage(
          failedFieldReviewMessage(failedFields),
          singleControlsForFieldErrors(failedFields.map((field) => ({ field }))),
        );
      }
      results.classList.add('show');
      results.scrollIntoView({ behavior: 'smooth', block: 'start' });
      focusAfterPaint(results);
    }

    function renderBatchResults(data) {
      const counts = data.summary || { total: 0, passed: 0, needs_review: 0, errors: 0 };
      const invalidControls = batchController.controlsForFailedResults(data.results || []);
      verdict.className = `verdict ${counts.errors || counts.needs_review ? 'review' : 'approved'}`;
      verdict.innerHTML = `
        <span class="verdict-title">${counts.errors || counts.needs_review ? 'NEEDS REVIEW' : 'APPROVED'}</span>
        <p class="verdict-copy">${counts.total} labels checked.</p>
      `;
      summary.innerHTML = `
        <div class="summary-grid">
          <div class="summary-box"><span class="summary-number">${counts.total}</span><span class="summary-label">Total</span></div>
          <div class="summary-box"><span class="summary-number">${counts.passed}</span><span class="summary-label">Approved</span></div>
          <div class="summary-box"><span class="summary-number">${counts.needs_review}</span><span class="summary-label">Needs Review</span></div>
          <div class="summary-box"><span class="summary-number">${counts.errors || 0}</span><span class="summary-label">Errors</span></div>
        </div>
      `;

      resultList.innerHTML = (data.results || []).map((item) => {
        const label = item.filename || `Label ${item.index + 1}`;
        const statusClass = item.status === 'APPROVED' ? 'pass' : item.status === 'ERROR' ? 'error' : 'review';
        const statusText = item.status === 'APPROVED' ? 'APPROVED' : item.status === 'ERROR' ? 'ERROR' : 'NEEDS REVIEW';
        const failedCount = item.result && Array.isArray(item.result.field_results)
          ? item.result.field_results.filter((field) => field.status === 'FAIL').length
          : 0;
        const detailId = `item-details-${item.index}`;
        const detailButton = item.result
          ? `<button class="secondary-action details-toggle" type="button" data-target="${detailId}" aria-expanded="false">View Details</button>`
          : '';
        const details = item.result
          ? `<div id="${detailId}" class="item-details">${renderFieldResults(item.result.field_results)}</div>`
          : '';
        const supportingText = item.status === 'ERROR'
          ? `<p class="reason">${escapeHtml(item.error || 'Something went wrong while checking this label. Please try again.')}</p>`
          : `<p class="value-line">${failedCount ? `${failedCount} field${failedCount === 1 ? '' : 's'} need a closer look.` : 'All fields matched.'}</p>`;

        return `
          <article class="result-row ${statusClass}">
            <div class="result-head">
              <div>
                <div class="result-name">${escapeHtml(label)}</div>
                ${supportingText}
              </div>
              <div class="badge ${statusClass}">${statusText}</div>
            </div>
            ${detailButton}
            ${details}
          </article>
        `;
      }).join('');

      time.textContent = typeof data.latency_ms === 'number'
        ? `Checked in ${(data.latency_ms / 1000).toFixed(1)} seconds.`
        : '';
      if (invalidControls.length) {
        showReviewMessage(
          `${invalidControls.length} field${invalidControls.length === 1 ? '' : 's'} need review. They are highlighted above.`,
          invalidControls,
        );
      }
      results.classList.add('show');
      results.scrollIntoView({ behavior: 'smooth', block: 'start' });
      focusAfterPaint(results);
    }

    async function submitForm(url, formData, labelCount, render, controlsForProblem = () => []) {
      setBusy(true, labelCount);
      try {
        const response = await fetch(url, {
          method: 'POST',
          body: formData,
        });

        let data = null;
        try {
          data = await response.json();
        } catch (error) {
          data = null;
        }

        if (!response.ok) {
          const problem = readProblem(data, response.status);
          showError(problem.message, controlsForProblem(problem));
          return;
        }

        render(data);
      } catch (error) {
        showError('The label checker could not be reached. Please try again.');
      } finally {
        setBusy(false);
      }
    }

    singleMode.addEventListener('click', () => setMode('single'));
    batchMode.addEventListener('click', () => setMode('batch'));

    imageInput.addEventListener('change', () => {
      renderImagePreview(imageInput, chosenFile, singlePreview, singlePreviewImage);
      clearError();
    });

    singleFields.forEach((field) => {
      field.element.addEventListener('input', clearError);
    });

    singleForm.addEventListener('submit', async (event) => {
      event.preventDefault();
      clearError();
      clearResults();

      if (!imageInput.files || !imageInput.files[0]) {
        showError('Please choose a label photo.', [imageInput]);
        return;
      }

      if (!singleHasAllFields()) {
        showError('Please fill in all seven boxes.', missingSingleControls());
        return;
      }

      const formData = new FormData();
      formData.append('image', imageInput.files[0]);
      formData.append('application_data', JSON.stringify(buildSinglePayload()));
      await submitForm(
        '/verify',
        formData,
        1,
        renderSingleResults,
        (problem) => singleControlsForFieldErrors(problem.fieldErrors),
      );
    });

    addLabelButton.addEventListener('click', () => {
      if (batchList.querySelectorAll('.batch-card').length >= maxBatchLabels) {
        showError('Please check 5 labels or fewer at one time.');
        return;
      }
      batchController.createCard();
    });

    batchForm.addEventListener('submit', async (event) => {
      event.preventDefault();
      clearError();
      clearResults();

      const items = batchController.getItems();
      if (items.length > maxBatchLabels) {
        showError('Please check 5 labels or fewer at one time.');
        return;
      }

      if (!items.every(batchController.itemIsComplete)) {
        showError('Each label needs one photo and all seven boxes filled in.', batchController.missingControls(items));
        return;
      }

      const formData = new FormData();
      const applicationData = [];
      items.forEach((item) => {
        formData.append('images', item.file);
        applicationData.push(item.values);
      });
      formData.append('application_data', JSON.stringify(applicationData));
      await submitForm(
        '/verify/batch',
        formData,
        items.length,
        renderBatchResults,
        (problem) => batchController.controlsForFieldErrors(problem.fieldErrors),
      );
    });

    resultList.addEventListener('click', (event) => {
      const button = event.target.closest('.details-toggle');
      if (!button) {
        return;
      }
      const target = document.getElementById(button.dataset.target);
      if (!target) {
        return;
      }
      const isOpen = target.classList.toggle('open');
      button.setAttribute('aria-expanded', String(isOpen));
      button.textContent = isOpen ? 'Hide Details' : 'View Details';
    });

    batchController.createCard();
