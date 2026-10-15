class GcomAccessCodesCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._codes = [];
    this._timer = null;
    this._loaded = false;
    this._generation = 0;
    this._creating = false;
    this.shadowRoot.innerHTML = `
      <style>
        :host { display:block; }
        ha-card { padding:16px; }
        label { display:block; margin:12px 0; }
        select, input { width:100%; box-sizing:border-box; padding:8px; }
        button { margin:4px 6px 4px 0; padding:8px; cursor:pointer; }
        .error { color:var(--error-color); }
        .code { font-family:monospace; white-space:pre-wrap; }
      </style>
      <ha-card header="10G.com access codes">
        <label>Account<select id="entry"></select></label>
        <label>Facility<select id="facility"></select></label>
        <button id="load" type="button">Load codes</button>
        <div id="codes"></div>
        <label>Code expiry (local time)<input id="expiry" type="datetime-local"></label>
        <button id="create" type="button">Create code</button>
        <p id="status" role="status"></p>
      </ha-card>`;
    this.shadowRoot.getElementById("entry").addEventListener("change", () => this._switchEntry());
    this.shadowRoot.getElementById("facility").addEventListener("change", () => this._clearCodes());
    this.shadowRoot.getElementById("load").addEventListener("click", () => this._loadCodes());
    this.shadowRoot.getElementById("create").addEventListener("click", () => this._createCode());
  }

  setConfig(config) { this._config = config || {}; }
  getCardSize() { return 4; }
  set hass(hass) {
    if (this._hass && this._hass.connection !== hass.connection) {
      this._clearCodes();
      this._loaded = false;
    }
    this._hass = hass;
    if (this.isConnected && !this._loaded) {
      this._loaded = true;
      this._loadEntries();
    }
  }

  connectedCallback() {
    if (this._hass && !this._loaded) {
      this._loaded = true;
      this._loadEntries();
    }
  }

  disconnectedCallback() {
    this._clearCodes();
    this._loaded = false;
  }

  _status(message, error = false) {
    const el = this.shadowRoot.getElementById("status");
    el.textContent = message;
    el.className = error ? "error" : "";
  }

  _clearCodes() {
    this._generation += 1;
    clearTimeout(this._timer);
    this._timer = null;
    this._codes = [];
    this.shadowRoot.getElementById("codes").replaceChildren();
  }

  _scheduleClear() {
    clearTimeout(this._timer);
    this._timer = setTimeout(() => {
      this._clearCodes();
      this._status("Codes cleared after 60 seconds.");
    }, 60000);
  }

  _fillSelect(id, items, label) {
    const select = this.shadowRoot.getElementById(id);
    select.replaceChildren();
    for (const item of items) {
      const option = document.createElement("option");
      option.value = item.id;
      option.textContent = item[label] || item.id;
      select.append(option);
    }
  }

  async _loadEntries() {
    const generation = this._generation;
    const connection = this._hass.connection;
    try {
      const entries = await connection.sendMessagePromise({ type:"gcom/entries" });
      if (!this.isConnected || generation !== this._generation || connection !== this._hass.connection) return;
      this._fillSelect("entry", entries.filter((entry) => entry.codes_enabled), "title");
      const desired = this._config.entry_id;
      if (desired && entries.some((entry) => entry.id === desired && entry.codes_enabled)) {
        this.shadowRoot.getElementById("entry").value = desired;
      }
      await this._switchEntry();
    } catch (_) {
      if (this.isConnected && generation === this._generation) this._status("Administrator access is required.", true);
    }
  }

  async _switchEntry() {
    this._clearCodes();
    const generation = this._generation;
    const entry_id = this.shadowRoot.getElementById("entry").value;
    if (!entry_id) { this._status("Enable access codes in integration options.", true); return; }
    try {
      const connection = this._hass.connection;
      const facilities = await connection.sendMessagePromise({ type:"gcom/facilities", entry_id });
      if (!this.isConnected || generation !== this._generation || connection !== this._hass.connection || entry_id !== this.shadowRoot.getElementById("entry").value) return;
      this._fillSelect("facility", facilities, "name");
      this._status("Select a facility, then load codes.");
    } catch (_) {
      if (this.isConnected && generation === this._generation) this._status("Facilities could not be loaded.", true);
    }
  }

  _renderCodes() {
    const box = this.shadowRoot.getElementById("codes");
    box.replaceChildren();
    const generation = this._generation;
    for (const item of this._codes) {
      const row = document.createElement("p");
      const code = document.createElement("span");
      code.className = "code";
      code.textContent = "Code hidden";
      const reveal = document.createElement("button");
      reveal.type = "button";
      reveal.textContent = "Reveal";
      const copy = document.createElement("button");
      copy.type = "button";
      copy.textContent = "Copy";
      copy.disabled = true;
      const isCurrent = () => this.isConnected && generation === this._generation && this._codes.includes(item);
      reveal.addEventListener("click", () => {
        if (!isCurrent()) return;
        code.textContent = item.code;
        reveal.disabled = true;
        copy.disabled = false;
      });
      copy.addEventListener("click", async () => {
        if (!isCurrent() || copy.disabled) return;
        if (!navigator.clipboard?.writeText) {
          this._status("Clipboard is unavailable in this browser.", true);
          return;
        }
        try {
          await navigator.clipboard.writeText(item.code);
          if (isCurrent()) this._status("Code copied to clipboard.");
        } catch (_) {
          if (isCurrent()) this._status("Code could not be copied.", true);
        }
      });
      row.append(code, document.createTextNode(` · Expires: ${item.expired || "unknown"} · `), reveal, copy);
      box.append(row);
    }
    this._scheduleClear();
  }

  async _loadCodes() {
    if (this._creating) return;
    this._clearCodes();
    const generation = this._generation;
    const entry_id = this.shadowRoot.getElementById("entry").value;
    const facility_id = this.shadowRoot.getElementById("facility").value;
    if (!entry_id || !facility_id) return;
    try {
      const connection = this._hass.connection;
      const codes = await connection.sendMessagePromise({ type:"gcom/access_codes/list", entry_id, facility_id });
      if (!this.isConnected || generation !== this._generation || connection !== this._hass.connection || entry_id !== this.shadowRoot.getElementById("entry").value || facility_id !== this.shadowRoot.getElementById("facility").value) return;
      this._codes = codes;
      this._renderCodes();
      this._status(`${this._codes.length} code(s) loaded. Hidden until revealed.`);
    } catch (_) {
      if (this.isConnected && generation === this._generation) this._status("Codes could not be loaded.", true);
    }
  }

  async _createCode() {
    if (this._creating) return;
    this._clearCodes();
    const generation = this._generation;
    const entry_id = this.shadowRoot.getElementById("entry").value;
    const facility_id = this.shadowRoot.getElementById("facility").value;
    const local = this.shadowRoot.getElementById("expiry").value;
    const expires = new Date(local);
    if (!entry_id || !facility_id || !local || !Number.isFinite(expires.valueOf()) || expires <= new Date()) {
      this._status("Choose a facility and future expiry.", true); return;
    }
    if (!window.confirm("Create a 10G.com access code for this facility?")) return;
    this._creating = true;
    const button = this.shadowRoot.getElementById("create");
    const loadButton = this.shadowRoot.getElementById("load");
    button.disabled = true;
    loadButton.disabled = true;
    try {
      const connection = this._hass.connection;
      const code = await connection.sendMessagePromise({
        type:"gcom/access_codes/create", entry_id, facility_id,
        expires_at:expires.toISOString(), confirmed:true,
      });
      if (!this.isConnected || generation !== this._generation || connection !== this._hass.connection || entry_id !== this.shadowRoot.getElementById("entry").value || facility_id !== this.shadowRoot.getElementById("facility").value) return;
      this._codes = [code];
      this._renderCodes();
      this._status("Code created. Reveal it to view; it clears after 60 seconds.");
    } catch (error) {
      if (!this.isConnected || generation !== this._generation || entry_id !== this.shadowRoot.getElementById("entry").value || facility_id !== this.shadowRoot.getElementById("facility").value) return;
      this._status(error?.code === "outcome_unknown"
        ? "Creation result unknown. Refresh the list before trying again."
        : "Code creation failed.", true);
    } finally {
      this._creating = false;
      button.disabled = false;
      loadButton.disabled = false;
    }
  }
}
customElements.define("gcom-access-codes-card", GcomAccessCodesCard);
window.customCards = window.customCards || [];
window.customCards.push({ type:"gcom-access-codes-card", name:"10G.com access codes", description:"Private administrator access-code panel" });
