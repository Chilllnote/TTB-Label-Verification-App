const batchFieldConfig = [
  ['brand', 'Brand', 'input'],
  ['class', 'Class or Type', 'input'],
  ['producer', 'Producer', 'input'],
  ['country', 'Country', 'input'],
  ['abv', 'Alcohol %', 'input'],
  ['net_contents', 'Bottle Size', 'input'],
  ['government_warning', 'Government Warning', 'textarea'],
];

function normalizeFieldName(field) {
  return field === 'product_class' ? 'class' : field;
}

export function createBatchController({
  batchList,
  addLabelButton,
  maxBatchLabels,
  clearError,
  renderImagePreview,
}) {
  let batchId = 0;

  function updateButtons() {
    const cards = batchList.querySelectorAll('.batch-card');
    addLabelButton.disabled = cards.length >= maxBatchLabels;
    cards.forEach((card, index) => {
      card.querySelector('.batch-title').textContent = `Label ${index + 1}`;
      const removeButton = card.querySelector('.remove-label');
      removeButton.disabled = cards.length === 1;
      removeButton.setAttribute('aria-label', `Remove label ${index + 1}`);
    });
  }

  function createCard() {
    batchId += 1;
    const card = document.createElement('article');
    card.className = 'batch-card';
    card.dataset.batchId = String(batchId);
    const prefix = `batch-${batchId}`;
    const fieldsHtml = batchFieldConfig.map(([key, label, type]) => {
      const id = `${prefix}-${key}`;
      const fieldClass = key === 'government_warning' ? 'field full' : 'field';
      const control = type === 'textarea'
        ? `<textarea id="${id}" data-batch-field="${key}" required></textarea>`
        : `<input id="${id}" data-batch-field="${key}" type="text" autocomplete="off" required />`;
      return `
        <div class="${fieldClass}">
          <label for="${id}">${label}</label>
          ${control}
        </div>
      `;
    }).join('');

    card.innerHTML = `
      <div class="batch-head">
        <h3 class="batch-title">Label</h3>
        <button class="secondary-action remove-label" type="button">Remove</button>
      </div>
      <div class="form-grid">
        <div class="field full">
          <label for="${prefix}-image">Choose Label Photo</label>
          <input id="${prefix}-image" data-batch-image type="file" accept="image/*" required aria-describedby="${prefix}-chosen-file" />
          <div id="${prefix}-chosen-file" class="chosen-file" aria-live="polite">No photo chosen yet.</div>
          <div class="image-preview" data-image-preview hidden>
            <img alt="Selected label preview" data-image-preview-img />
          </div>
        </div>
        ${fieldsHtml}
      </div>
    `;

    const fileInput = card.querySelector('[data-batch-image]');
    const fileLabel = card.querySelector('.chosen-file');
    const preview = card.querySelector('[data-image-preview]');
    const previewImage = card.querySelector('[data-image-preview-img]');
    fileInput.addEventListener('change', () => {
      renderImagePreview(fileInput, fileLabel, preview, previewImage);
      clearError();
    });
    card.querySelector('.remove-label').addEventListener('click', () => {
      const objectUrl = preview.dataset.objectUrl;
      if (objectUrl) {
        URL.revokeObjectURL(objectUrl);
      }
      card.remove();
      updateButtons();
      clearError();
    });
    card.querySelectorAll('input[type="text"], textarea').forEach((control) => {
      control.addEventListener('input', clearError);
    });

    batchList.appendChild(card);
    updateButtons();
  }

  function getItems() {
    return Array.from(batchList.querySelectorAll('.batch-card')).map((card) => {
      const image = card.querySelector('[data-batch-image]');
      const values = {};
      card.querySelectorAll('[data-batch-field]').forEach((control) => {
        values[control.dataset.batchField] = control.value.trim();
      });
      return {
        card,
        file: image.files && image.files[0],
        values,
      };
    });
  }

  function itemIsComplete(item) {
    return item.file && batchFieldConfig.every(([key]) => item.values[key]);
  }

  function missingControls(items) {
    const missing = [];
    items.forEach((item) => {
      const image = item.card.querySelector('[data-batch-image]');
      if (!item.file) {
        missing.push(image);
      }
      item.card.querySelectorAll('[data-batch-field]').forEach((control) => {
        if (!control.value.trim()) {
          missing.push(control);
        }
      });
    });
    return missing;
  }

  function controlsForFieldErrors(fieldErrors = []) {
    return fieldErrors.map((error) => {
      const index = Number(error.index);
      const cards = Array.from(batchList.querySelectorAll('.batch-card'));
      const card = Number.isInteger(index) ? cards[index] : null;
      const field = normalizeFieldName(error.field);
      return card ? card.querySelector(`[data-batch-field="${field}"]`) : null;
    }).filter(Boolean);
  }

  function controlsForFailedResults(results = []) {
    const controls = [];
    const cards = Array.from(batchList.querySelectorAll('.batch-card'));
    results.forEach((item) => {
      const card = cards[item.index];
      if (!card || !item.result || !Array.isArray(item.result.field_results)) {
        return;
      }

      item.result.field_results.forEach((fieldResult) => {
        if (fieldResult.status !== 'FAIL') {
          return;
        }
        const field = normalizeFieldName(fieldResult.field);
        const control = card.querySelector(`[data-batch-field="${field}"]`);
        if (control) {
          controls.push(control);
        }
      });
    });
    return controls;
  }

  return {
    createCard,
    getItems,
    itemIsComplete,
    missingControls,
    controlsForFieldErrors,
    controlsForFailedResults,
    updateButtons,
  };
}
