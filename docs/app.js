/* =====================================================================
 * EU Entry-Level Jobs Dashboard - vanilla JS, no build step.
 *
 * Data contract (bound to the aggregator's real schemas):
 *   data/active_jobs.json : object keyed by uid; each record has the
 *     fields of src.models.Job - title, title_en, company, country,
 *     location, posted_date, source, url, score, signals,
 *     matched_keywords, first_seen, last_seen, excluded, ...
 *   data/run_status.json  : written by main.py after every run:
 *     {last_run_utc, duration_sec, parameters:{tier, countries,
 *     sources, max_jobs, min_score}, totals:{fetched, excluded,
 *     below_min_score, translated_titles, new, updated, pruned,
 *     active}, countries:{CC: {source: {status, fetched, errors,
 *     notes}}}}
 * Both are fetched with relative paths so the site works under any
 * GitHub Pages base path.
 *
 * Rendering rule: all dynamic text goes through textContent - never
 * innerHTML - so scraped posting content cannot inject markup.
 * =================================================================== */
'use strict';

(function () {

  /* ---------------- static reference data ---------------- */

  // Country metadata mirrors config/config.yaml (tiers + names). The
  // config is YAML, so the dashboard keeps its own copy; tests assert
  // this map stays in sync with the config.
  var COUNTRY_META = {
    PL: { name: 'Poland',          tier: 1 },
    CZ: { name: 'Czech Republic',  tier: 1 },
    DE: { name: 'Germany',         tier: 1 },
    LT: { name: 'Lithuania',       tier: 1 },
    HU: { name: 'Hungary',         tier: 1 },
    RO: { name: 'Romania',         tier: 1 },
    SK: { name: 'Slovakia',        tier: 1 },
    NL: { name: 'Netherlands',     tier: 1 },
    ES: { name: 'Spain',           tier: 2 },
    IT: { name: 'Italy',           tier: 2 },
    FR: { name: 'France',          tier: 2 },
    BE: { name: 'Belgium',         tier: 2 },
    PT: { name: 'Portugal',        tier: 2 },
    IE: { name: 'Ireland',         tier: 2 },
    AT: { name: 'Austria',         tier: 3 },
    BG: { name: 'Bulgaria',        tier: 3 },
    HR: { name: 'Croatia',         tier: 3 },
    CY: { name: 'Cyprus',          tier: 3 },
    DK: { name: 'Denmark',         tier: 3 },
    EE: { name: 'Estonia',         tier: 3 },
    FI: { name: 'Finland',         tier: 3 },
    GR: { name: 'Greece',          tier: 3 },
    LV: { name: 'Latvia',          tier: 3 },
    LU: { name: 'Luxembourg',      tier: 3 },
    MT: { name: 'Malta',           tier: 3 },
    SI: { name: 'Slovenia',        tier: 3 },
    SE: { name: 'Sweden',          tier: 3 }
  };

  // Signal chips. Labels are deliberately worded as MENTIONS: a keyword
  // match in the posting text is a review hint, never proof that the
  // employer provides the benefit.
  var SIGNAL_META = {
    no_exp_or_training_mention:    { label: 'No experience / Training', hint: 'The posting text says no experience is required or training is provided - a mention to verify, not a guarantee.' },
    permit_keyword_mention:        { label: 'Work permit mentioned',  hint: 'The posting text mentions work permits or visas - not proof of sponsorship.' },
    housing_keyword_mention:       { label: 'Housing mentioned',      hint: 'The posting text mentions accommodation - not proof housing is provided.' },
    language_flex_keyword_mention: { label: 'Language flexibility',   hint: 'The posting text suggests no local language is required.' },
    employment_agency_source:      { label: 'Employment agency',      hint: 'The source publishing this job is a verified employment agency - a fact about the source, not a promise of relocation, housing or permits.' },
    agency_relocation_text_mention:{ label: 'Agency / relocation mentioned', hint: 'The posting text mentions agency or relocation words - a mention to verify, not a provided benefit.' },
    agency_or_relocation_mention:  { label: 'Agency / relocation (legacy)', hint: 'Recorded before source-origin and text-mention were separated - could be either; verify directly with the employer or agency.' },
    fresh_posting:                 { label: 'Fresh posting',          hint: 'Posted within the last 14 days.' }
  };
  var CHIP_ORDER = [
    'no_exp_or_training_mention',
    'permit_keyword_mention',
    'housing_keyword_mention',
    'language_flex_keyword_mention',
    'employment_agency_source',
    'agency_relocation_text_mention',
    'agency_or_relocation_mention',
    'fresh_posting'
  ];

  var SOURCE_LABELS = {
    eures: 'EURES',
    jobspy_indeed: 'Indeed',
    jobspy_linkedin: 'LinkedIn',
    arbeitnow: 'Arbeitnow',
    sweden_jobsearch: 'Arbetsf\u00f6rmedlingen (SE)',
    czech_mpsv: 'MPSV vacancies (CZ)',
    latvia_nva: 'NVA (LV)',
    ottoworkforce: 'OTTO Work Force (NL)',
    nl_jobs: 'NL Jobs (NL)',
    hobij: 'Hobij (NL)',
    pl_cbop: 'CBOP - Polish PES (PL)',
    anyworkanywhere: 'AnyworkAnywhere',
    ats_public: 'Direct employer portals'
  };

  var HEALTH_ORDER = ['failed', 'partial', 'skipped', 'ok'];

  /* ---------------- small helpers ---------------- */

  function el(tag, className, text) {
    var node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined && text !== null) node.textContent = String(text);
    return node;
  }


  function sourceLabel(source) {
    return SOURCE_LABELS[source] || source || 'Unknown';
  }

  function countryName(code) {
    var meta = COUNTRY_META[code];
    return meta ? meta.name : code;
  }

  function countryTier(code) {
    var meta = COUNTRY_META[code];
    return meta ? meta.tier : null;
  }

  function formatDay(isoDay) {
    if (!isoDay) return '';
    var d = new Date(isoDay);
    if (isNaN(d.getTime())) return String(isoDay);
    return d.toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric' });
  }

  function formatDateTime(iso) {
    if (!iso) return '';
    var d = new Date(iso);
    if (isNaN(d.getTime())) return String(iso);
    return d.toLocaleString('en-GB', {
      day: 'numeric', month: 'short', year: 'numeric',
      hour: '2-digit', minute: '2-digit', timeZoneName: 'short'
    });
  }

  function relativeAge(iso) {
    var d = new Date(iso);
    if (isNaN(d.getTime())) return '';
    var hours = Math.round((Date.now() - d.getTime()) / 3600000);
    if (hours < 1) return 'less than an hour ago';
    if (hours < 48) return hours + ' hour' + (hours === 1 ? '' : 's') + ' ago';
    var days = Math.round(hours / 24);
    return days + ' day' + (days === 1 ? '' : 's') + ' ago';
  }

  // Only http(s) URLs ever become links. Anything else (script or data
  // pseudo-protocols, malformed values) renders as plain text.
  function safeExternalUrl(raw) {
    if (typeof raw !== 'string' || raw.length === 0 || raw.length > 2048) return null;
    var parsed;
    try {
      parsed = new URL(raw);
    } catch (e) {
      return null;
    }
    if (parsed.protocol !== 'https:' && parsed.protocol !== 'http:') return null;
    return parsed.href;
  }

  function jobDate(job) {
    return job.posted_date || job.first_seen || '';
  }

  /* ---------------- state ---------------- */

  var allJobs = [];
  var runStatus = null;

  var controls = {
    search: document.getElementById('search'),
    country: document.getElementById('filter-country'),
    tier: document.getElementById('filter-tier'),
    source: document.getElementById('filter-source'),
    minScore: document.getElementById('filter-min-score'),
    minScoreValue: document.getElementById('min-score-value'),
    signals: Array.prototype.slice.call(
      document.querySelectorAll('.control-signals input[type="checkbox"]')),
    sort: document.getElementById('sort-by')
  };
  var jobList = document.getElementById('job-list');
  var resultCount = document.getElementById('result-count');

  function showState(id) {
    ['state-loading', 'state-error', 'state-empty-store', 'state-no-results']
      .forEach(function (sid) {
        document.getElementById(sid).hidden = (sid !== id);
      });
  }
  function hideStates() { showState('__none__'); }

  /* ---------------- status banner ---------------- */

  function worstHealth(sources) {
    var worst = 'unknown';
    var keys = Object.keys(sources || {});
    for (var i = 0; i < keys.length; i++) {
      var st = (sources[keys[i]] && sources[keys[i]].status) || 'unknown';
      if (HEALTH_ORDER.indexOf(st) < HEALTH_ORDER.indexOf(worst) || worst === 'unknown') {
        if (worst === 'unknown' || HEALTH_ORDER.indexOf(st) < HEALTH_ORDER.indexOf(worst)) {
          worst = st;
        }
      }
    }
    return worst;
  }

  function renderStatus(status) {
    var summary = document.getElementById('status-summary');
    var totalsBox = document.getElementById('status-totals');
    var countriesBox = document.getElementById('status-countries');
    var note = document.getElementById('status-note');
    totalsBox.textContent = '';
    countriesBox.textContent = '';

    if (!status) {
      summary.textContent = 'No automated run recorded yet - the first scheduled scrape will publish its status here.';
      note.hidden = true;
      return;
    }

    var params = status.parameters || {};
    var when = formatDateTime(status.last_run_utc);
    var age = relativeAge(status.last_run_utc);
    var countryCount = (params.countries || []).length;
    var duration = (typeof status.duration_sec === 'number')
      ? ', completed in ' + Math.round(status.duration_sec) + 's'
      : '';
    summary.textContent =
      'Last automated run: ' + when + (age ? ' (' + age + ')' : '') +
      ' - tier ' + (params.tier || '?') +
      ', ' + countryCount + ' ' + (countryCount === 1 ? 'country' : 'countries') +
      ', sources ' + (params.sources || []).join(' + ') + duration;

    var totals = status.totals || {};
    [['active', 'active'], ['new', 'new'], ['updated', 'updated'],
     ['pruned', 'pruned'], ['fetched', 'fetched'],
     ['translated_titles', 'titles translated']]
      .forEach(function (pair) {
        if (typeof totals[pair[0]] === 'number') {
          totalsBox.appendChild(
            el('span', 'status-total', totals[pair[0]] + ' ' + pair[1]));
        }
      });

    var countries = status.countries || {};
    var codes = Object.keys(countries).sort(function (a, b) {
      var ta = countryTier(a) || 9, tb = countryTier(b) || 9;
      if (ta !== tb) return ta - tb;
      return countryName(a).localeCompare(countryName(b));
    });

    codes.forEach(function (code) {
      var sources = countries[code] || {};
      var health = worstHealth(sources);
      var fetched = Object.keys(sources).reduce(function (sum, src) {
        return sum + ((sources[src] && sources[src].fetched) || 0);
      }, 0);

      var chip = el('span', 'country-health');
      chip.appendChild(el('span', 'dot dot-' + health));
      chip.appendChild(el('span', null, code));
      chip.appendChild(el('span', 'health-fetched', String(fetched)));

      var detail = Object.keys(sources).map(function (src) {
        var s = sources[src] || {};
        return sourceLabel(src) + ': ' + (s.status || '?') +
               ' (' + (s.fetched || 0) + ' fetched)';
      }).join(' - ');
      chip.title = countryName(code) + ' - ' + detail;
      countriesBox.appendChild(chip);

      var errors = [];
      Object.keys(sources).forEach(function (src) {
        ((sources[src] && sources[src].errors) || []).slice(0, 2).forEach(function (err) {
          errors.push(code + ' / ' + sourceLabel(src) + ': ' + err);
        });
      });
      if (errors.length) {
        var errBox = el('div', 'country-errors');
        errors.forEach(function (msg) { errBox.appendChild(el('div', null, msg)); });
        countriesBox.appendChild(errBox);
      }
    });

    // run_status.json reflects the LAST run only. The weekly scan runs
    // tiers sequentially and each tier overwrites the file, so after a
    // weekly run this banner shows the last tier that ran - not the
    // health of all 27 countries. Historical snapshots are uploaded as
    // workflow artifacts instead.
    note.textContent =
      'This reflects the most recent run only. The weekly full scan ' +
      'overwrites the status after each sequential tier, so right after ' +
      'it this banner shows that scan\'s last tier, not all 27 countries.';
    note.hidden = false;
  }

  /* ---------------- job cards ---------------- */

  function scoreClass(score) {
    if (score >= 70) return 'score-high';
    if (score >= 40) return 'score-mid';
    return 'score-low';
  }

  function renderCard(job) {
    var card = el('article', 'job-card');

    var top = el('div', 'job-top');
    var code = (job.country || '').toUpperCase();
    top.appendChild(el('span', 'badge badge-country',
      (code || '??') + ' - ' + countryName(code)));
    var tier = countryTier(code);
    top.appendChild(el('span', 'badge badge-tier',
      tier ? 'Tier ' + tier : 'Tier unknown'));
    top.appendChild(el('span', 'badge badge-source', sourceLabel(job.source)));
    top.appendChild(el('span', 'badge badge-score ' + scoreClass(job.score || 0),
      'Score ' + (job.score != null ? job.score : 0)));
    card.appendChild(top);

    var titles = el('div', 'job-titles');
    // A title_en identical to the source title is NOT a translation
    // (legacy failure-copies pending repair): never present it as English.
    var hasRealEn = !!(job.title_en && job.title && job.title_en !== job.title);
    if (hasRealEn) {
      titles.appendChild(el('h3', 'job-title-en', job.title_en));
      titles.appendChild(el('p', 'job-title-orig', job.title));
    } else {
      titles.appendChild(el('h3', 'job-title-orig', job.title || '(untitled posting)'));
      if (job.title_en === job.title && job.title) {
        titles.appendChild(el('p', 'job-title-pending', 'Translation pending'));
      }
    }
    if (job.title_ar) {
      var arTitle = el('p', 'job-title-ar', job.title_ar);
      arTitle.setAttribute('dir', 'rtl');
      arTitle.setAttribute('lang', 'ar');
      titles.appendChild(arTitle);
    }
    card.appendChild(titles);

    var metaBits = [];
    if (job.company) metaBits.push(job.company);
    if (job.location) metaBits.push(job.location);
    var when = formatDay(jobDate(job));
    if (when) metaBits.push('posted ' + when);
    card.appendChild(el('p', 'job-meta', metaBits.join(' - ')));

    // Legacy-data guard (computed before any rendering that depends on it):
    // jobs scored before the scorer fix may carry no_exp_or_training_mention
    // solely from a career-changer keyword (e.g. "quereinsteiger"), which is
    // NOT evidence of "no experience / training".
    var CAREER_CHANGER_TERMS = ['quereinsteiger', 'quereinstieg', 'career changer', 'berufswechsler'];
    var REAL_NOEXP_TERMS = ["apmacam",  "apmaciba",  "apmokome",  "be darbo patirties",  "be patirties",  "betanitas",  "betanitast biztositunk",  "betanitott munka",  "bez doswiadczenia",  "bez doswiadczenia zawodowego",  "bez iskustva",  "bez pieredzes",  "bez praxe",  "bez predchadzajucej praxe",  "bez predchozi praxe",  "brez izkusenj",  "com formacao",  "con formacion",  "con formazione",  "debutant accepte",  "do przyuczenia",  "doswiadczenie nie jest wymagane",  "ei aikaisempaa kokemusta",  "ei kokemusta",  "entry level",  "experience non requise",  "fara experienta",  "fara experienta necesara",  "fara experienta prealabila",  "formacao fornecida",  "formacion a cargo de la empresa",  "formation assuree",  "formazione fornita",  "full training",  "full training provided",  "geen ervaring nodig",  "geen ervaring vereist",  "ilma kogemuseta",  "ingen erfarenhet",  "ingen erfarenhet kravs",  "ingen erfaring",  "ingen erfaring kraves",  "inwerktraject",  "iskustvo nije potrebno",  "izkusnje niso potrebne",  "keine erfahrung erforderlich",  "keine vorkenntnisse",  "kogemus pole vajalik",  "kogemust ei nouta",  "kokemusta ei vaadita",  "koulutetaan",  "mit einarbeitung",  "nao requer experiencia",  "nincs szukseg tapasztalatra",  "no experience",  "no experience necessary",  "no experience needed",  "no experience required",  "no previous experience",  "no se requiere experiencia",  "non e richiesta esperienza",  "nu este necesara experienta",  "obuka",  "ohne berufserfahrung",  "ohne erfahrung",  "ohne vorkenntnisse",  "on the job training",  "on-the-job training",  "oplaering",  "osigurana obuka",  "patirties nereikia",  "pieredze nav nepieciesama",  "prax nie je nutna",  "praxe neni nutna",  "primo impiego",  "przyuczymy",  "sans experience",  "sans experience prealable",  "sem experiencia",  "sem experiencia necessaria",  "senza esperienza",  "sin experiencia",  "sin experiencia previa",  "szkolenie zapewnione",  "tapasztalat nelkul",  "trainee",  "training asigurat",  "training given",  "training provided",  "uden erfaring",  "usposabljanje",  "utan erfarenhet",  "utbildning ges",  "valjaope",  "vi lar dig",  "wij leren je het vak",  "will train",  "wir lernen dich an",  "without experience",  "wordt ingewerkt",  "zagotovljeno usposabljanje",  "zapewniamy szkolenie",  "zaskolime",  "zaucime",  "zonder ervaring",  "zonder werkervaring",  "εμπειρια δεν απαιτειται",  "παρεχεται εκπαιδευση",  "χωρις εμπειρια",  "χωρις προϋπηρεσια",  "без опит",  "без предишен опит",  "обучение се осигурява",  "опит не се изисква"];
    var matchedKw = (Array.isArray(job.matched_keywords) ? job.matched_keywords : []).map(function (k) {
      return String(k).toLowerCase();
    });
    var noexpSupported = (function () {
      var hasReal = REAL_NOEXP_TERMS.some(function (t) { return matchedKw.indexOf(t) !== -1; });
      if (hasReal) return true;
      var onlyCareerChanger = CAREER_CHANGER_TERMS.some(function (t) { return matchedKw.indexOf(t) !== -1; });
      // Suppress only when career-changer is the visible evidence; unknown
      // provenance stays reviewable rather than silently hidden.
      return !onlyCareerChanger;
    })();

    if (job.summary_ar) {
      var sumText = job.summary_ar;
      // Always normalize legacy stored mention lines: old data repeated the
      // per-item prefix ("يذكر الإعلان: X؛ يذكر الإعلان: Y"). The unsupported
      // no-experience/training mention is dropped only when unsupported;
      // every other backed fact stays untouched.
      sumText = sumText.split('\n').map(function (line) {
        var NEW_MARKER = 'عبارات وردت في نص الإعلان: ';
        var OLD_MARKER = 'المزايا المذكورة في نص الإعلان: ';
        var marker = line.indexOf(NEW_MARKER) === 0 ? NEW_MARKER
          : (line.indexOf(OLD_MARKER) === 0 ? OLD_MARKER : null);
        if (!marker) return line;
        var items = line.slice(marker.length).split('؛ ').map(function (it) {
          return it.replace(/^يذكر الإعلان: /, '');
        }).filter(function (it) {
          return noexpSupported || it.indexOf('لا خبرة') === -1;
        });
        return items.length ? NEW_MARKER + items.join('؛ ') : '';
      }).filter(function (line) { return line !== ''; }).join('\n');
      if (sumText) {
        // Provenance honesty: this block is generated from structured
        // facts (mentions, salary, location), never a translation of the
        // description - the label keeps that distinction visible.
        var arSumLabel = el('p', 'job-summary-ar-label',
          'حقائق منظمة من بيانات الإعلان - ليست ترجمة كاملة للوصف.');
        arSumLabel.setAttribute('dir', 'rtl');
        arSumLabel.setAttribute('lang', 'ar');
        card.appendChild(arSumLabel);
        var arSum = el('p', 'job-summary-ar', sumText);
        arSum.setAttribute('dir', 'rtl');
        arSum.setAttribute('lang', 'ar');
        card.appendChild(arSum);
      }
    }

    var signals = Array.isArray(job.signals) ? job.signals : [];
    var chips = el('div', 'job-chips');
    function chipSupported(sig) {
      if (sig !== 'no_exp_or_training_mention') return true;
      return noexpSupported;
    }
    CHIP_ORDER.forEach(function (sig) {
      if (signals.indexOf(sig) !== -1 && chipSupported(sig)) {
        var meta = SIGNAL_META[sig];
        var chip = el('span', 'chip', meta.label);
        chip.title = meta.hint;
        chips.appendChild(chip);
      }
    });
    if (chips.childNodes.length) card.appendChild(chips);

    if (Array.isArray(job.matched_keywords) && job.matched_keywords.length) {
      card.appendChild(el('p', 'job-keywords',
        'Matched: ' + job.matched_keywords.slice(0, 8).join(', ')));
    }

    var actions = el('div', 'job-actions');
    var href = safeExternalUrl(job.url);
    if (href) {
      var linkPending = job.url_provenance === 'reconstructed_from_source_key' && job.url_verified === false;
      var link = el('a', linkPending ? 'apply-link apply-link-pending' : 'apply-link',
        linkPending ? 'View posting (link rebuilt, unverified)' : 'View & apply');
      link.href = href;
      link.target = '_blank';
      link.rel = 'noopener noreferrer';
      actions.appendChild(link);
      // Every link is shown with its actual verification state: no link on
      // this dashboard has been individually HTTP-checked, so a solid
      // 'View & apply' must never read as verification proof.
      var provText, provTextAr;
      if (linkPending) {
        provText = 'Link rebuilt from the posting key at the source; not yet live-verified.';
        provTextAr = 'الرابط أُعيد بناؤه من مفتاح الإعلان لدى المصدر؛ لم يُتحقق منه بعد.';
      } else {
        provText = 'Link as listed by the source; not individually live-checked.';
        provTextAr = 'الرابط كما ورد في صفحة المصدر؛ لم يُتحقق منه فرديًا.';
      }
      var prov = el('p', 'link-provenance', provText);
      var provAr = el('span', 'ar', provTextAr);
      provAr.setAttribute('lang', 'ar');
      provAr.setAttribute('dir', 'rtl');
      prov.appendChild(provAr);
      actions.appendChild(prov);
    } else {
      actions.appendChild(el('span', 'apply-unavailable', 'Direct link unavailable'));
    }
    card.appendChild(actions);

    return card;
  }

  /* ---------------- filtering & sorting ---------------- */

  function selectedSignals() {
    return controls.signals
      .filter(function (box) { return box.checked; })
      .map(function (box) { return box.value; });
  }

  function currentJobs() {
    var query = controls.search.value.trim().toLowerCase();
    var country = controls.country.value;
    var tier = controls.tier.value;
    var source = controls.source.value;
    var minScore = parseInt(controls.minScore.value, 10) || 0;
    var wantedSignals = selectedSignals();

    var filtered = allJobs.filter(function (job) {
      if (job.excluded === true) return false;
      if (country && job.country !== country) return false;
      if (tier && String(countryTier(job.country)) !== tier) return false;
      if (source && job.source !== source) return false;
      if ((job.score || 0) < minScore) return false;
      var signals = Array.isArray(job.signals) ? job.signals : [];
      for (var i = 0; i < wantedSignals.length; i++) {
        if (signals.indexOf(wantedSignals[i]) === -1) return false;
      }
      if (query) {
        var haystack = [
          job.title, job.title_en, job.company, job.location,
          (job.matched_keywords || []).join(' ')
        ].join(' ').toLowerCase();
        if (haystack.indexOf(query) === -1) return false;
      }
      return true;
    });

    var sort = controls.sort.value;
    filtered.sort(function (a, b) {
      if (sort === 'newest') {
        var da = jobDate(a), db = jobDate(b);
        if (da !== db) return da < db ? 1 : -1;
        return (b.score || 0) - (a.score || 0);
      }
      if (sort === 'country') {
        var ta = countryTier(a.country) || 9, tb = countryTier(b.country) || 9;
        if (ta !== tb) return ta - tb;
        var cn = countryName(a.country).localeCompare(countryName(b.country));
        if (cn !== 0) return cn;
        return (b.score || 0) - (a.score || 0);
      }
      var byScore = (b.score || 0) - (a.score || 0);
      if (byScore !== 0) return byScore;
      var pa = jobDate(a), pb = jobDate(b);
      return pa < pb ? 1 : (pa > pb ? -1 : 0);
    });

    return filtered;
  }

  function render() {
    var jobs = currentJobs();
    jobList.textContent = '';
    var fragment = document.createDocumentFragment();
    jobs.forEach(function (job) { fragment.appendChild(renderCard(job)); });
    jobList.appendChild(fragment);

    if (allJobs.length === 0) {
      showState('state-empty-store');
      resultCount.textContent = '';
      return;
    }
    if (jobs.length === 0) {
      showState('state-no-results');
      resultCount.textContent = '0 of ' + allJobs.length + ' vacancies shown';
      return;
    }
    hideStates();
    resultCount.textContent = '';
    resultCount.appendChild(document.createTextNode(
      jobs.length + ' of ' + allJobs.length +
      (allJobs.length === 1 ? ' vacancy' : ' vacancies') + ' shown'));
    var arCount = el('span', 'ar ar-count-line',
      '\u0627\u0644\u0645\u0639\u0631\u0648\u0636: ' + jobs.length +
      ' \u0645\u0646 \u0623\u0635\u0644 ' + allJobs.length +
      ' \u0648\u0638\u064a\u0641\u0629');
    arCount.setAttribute('lang', 'ar');
    arCount.setAttribute('dir', 'rtl');
    resultCount.appendChild(arCount);
  }

  /* ---------------- data loading ---------------- */

  function populateSourceFilter() {
    var counts = {};
    allJobs.forEach(function (job) {
      counts[job.source] = (counts[job.source] || 0) + 1;
    });
    var codes = Object.keys(counts).sort(function (a, b) {
      return sourceLabel(a).localeCompare(sourceLabel(b));
    });
    codes.forEach(function (code) {
      var option = el('option', null,
        sourceLabel(code) + ' (' + counts[code] + ')');
      option.value = code;
      controls.source.appendChild(option);
    });
  }

  function populateCountryFilter() {
    var counts = {};
    allJobs.forEach(function (job) {
      counts[job.country] = (counts[job.country] || 0) + 1;
    });
    var codes = Object.keys(counts).sort(function (a, b) {
      var ta = countryTier(a) || 9, tb = countryTier(b) || 9;
      if (ta !== tb) return ta - tb;
      return countryName(a).localeCompare(countryName(b));
    });
    codes.forEach(function (code) {
      var option = el('option', null,
        countryName(code) + ' (' + counts[code] + ')');
      option.value = code;
      controls.country.appendChild(option);
    });
  }

  function fetchJson(path) {
    return fetch(path, { cache: 'no-store' }).then(function (resp) {
      if (resp.status === 404) return null;
      if (!resp.ok) throw new Error(path + ' returned HTTP ' + resp.status);
      return resp.json();
    });
  }

  function checkCsvAvailability() {
    var button = document.getElementById('csv-download');
    fetch('data/latest_export.csv', { method: 'HEAD', cache: 'no-store' })
      .then(function (resp) {
        if (!resp.ok) {
          button.setAttribute('aria-disabled', 'true');
          button.removeAttribute('href');
          button.textContent = 'CSV available after first scrape';
        }
      })
      .catch(function () { /* leave the link as-is */ });
  }

  function boot() {
    showState('state-loading');
    Promise.all([
      fetchJson('data/active_jobs.json'),
      fetchJson('data/run_status.json')
    ]).then(function (results) {
      var store = results[0];
      runStatus = results[1];
      if (store && typeof store === 'object' && !Array.isArray(store)) {
        allJobs = Object.keys(store).map(function (uid) {
          var record = store[uid] || {};
          if (!record.uid) record.uid = uid;
          return record;
        }).filter(function (record) {
          return record.excluded !== true;  // excluded records never display or count
        });
      } else if (store === null) {
        allJobs = [];
      } else {
        throw new Error('data/active_jobs.json has an unexpected shape');
      }
      renderStatus(runStatus);
      populateCountryFilter();
      populateSourceFilter();
      checkCsvAvailability();
      render();
    }).catch(function (err) {
      showState('state-error');
      document.getElementById('state-error-detail').textContent =
        (err && err.message) ? err.message : String(err);
      resultCount.textContent = '';
      renderStatus(runStatus);
    });
  }

  /* ---------------- events ---------------- */

  controls.search.addEventListener('input', render);
  controls.country.addEventListener('change', render);
  controls.tier.addEventListener('change', render);
  controls.source.addEventListener('change', render);
  controls.sort.addEventListener('change', render);
  controls.minScore.addEventListener('input', function () {
    controls.minScoreValue.textContent = controls.minScore.value;
    render();
  });
  controls.signals.forEach(function (box) {
    box.addEventListener('change', render);
  });

  boot();
})();


  /* Open the filter panel by default on desktop widths so the extra
     controls are visible; keep it collapsed on phones to save space. */
  (function () {
    var panel = document.getElementById('filters-panel');
    if (!panel || !window.matchMedia) return;
    var mq = window.matchMedia('(min-width: 720px)');
    function apply() { panel.open = mq.matches; }
    if (mq.addEventListener) mq.addEventListener('change', apply);
    else if (mq.addListener) mq.addListener(apply);
    apply();
  })();
