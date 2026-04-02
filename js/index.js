'use strict';

(async () => {
  let allPitches = [];

  const SS = {
    get: k  => sessionStorage.getItem('statscast_' + k) || '',
    set: (k, v) => sessionStorage.setItem('statscast_' + k, v),
  };

  const dataStatus     = document.getElementById('data-status');
  const uploadArea     = document.getElementById('upload-area');
  const csvInput       = document.getElementById('csv-input');
  const listSection    = document.getElementById('list-section');
  const clearBtn       = document.getElementById('clear-data-btn');

  // 打者フィルター要素
  const searchInput    = document.getElementById('search-input');
  const yearFilter     = document.getElementById('year-filter');
  const teamFilter     = document.getElementById('team-filter');

  // 投手フィルター要素
  const pitcherSearch  = document.getElementById('pitcher-search-input');
  const pitcherYear    = document.getElementById('pitcher-year-filter');
  const pitcherTeam    = document.getElementById('pitcher-team-filter');

  // リスト種別タブ
  const listTabs = document.querySelectorAll('[data-list]');
  const batterArea  = document.getElementById('batter-list-area');
  const pitcherArea = document.getElementById('pitcher-list-area');

  let activeList = SS.get('active_list') || 'batter';
  applyListTab(activeList);

  listTabs.forEach(btn => {
    btn.addEventListener('click', () => {
      activeList = btn.dataset.list;
      SS.set('active_list', activeList);
      applyListTab(activeList);
    });
  });

  function applyListTab(type) {
    listTabs.forEach(b => b.classList.toggle('active', b.dataset.list === type));
    batterArea.style.display  = type === 'batter'  ? '' : 'none';
    pitcherArea.style.display = type === 'pitcher' ? '' : 'none';
  }

  // --- データ読み込み ---
  setStatus('データを確認中...', 'loading');
  const filePitches = await STATSCAST.tryLoadFromFile();
  if (filePitches && filePitches.length > 0) {
    allPitches = filePitches;
    STATSCAST.saveData(allPitches);
    setStatus(`data/data.csv から ${allPitches.length} 球を読み込みました`, 'success');
    showLists(allPitches);
  } else {
    allPitches = STATSCAST.loadStoredData();
    if (allPitches.length > 0) {
      setStatus(`保存済みデータ ${allPitches.length} 球を読み込みました`, 'success');
      showLists(allPitches);
    } else {
      setStatus('CSVまたはExcelファイルをアップロードしてください', '');
    }
  }

  // --- ファイルアップロード ---
  uploadArea.addEventListener('click', () => csvInput.click());
  uploadArea.addEventListener('dragover', e => { e.preventDefault(); uploadArea.classList.add('dragover'); });
  uploadArea.addEventListener('dragleave', () => uploadArea.classList.remove('dragover'));
  uploadArea.addEventListener('drop', e => {
    e.preventDefault();
    uploadArea.classList.remove('dragover');
    handleFiles(Array.from(e.dataTransfer.files));
  });
  csvInput.addEventListener('change', e => { handleFiles(Array.from(e.target.files)); e.target.value = ''; });

  clearBtn.addEventListener('click', () => {
    if (!confirm('保存済みデータを削除しますか？')) return;
    STATSCAST.clearData();
    allPitches = [];
    listSection.style.display = 'none';
    setStatus('データを削除しました', '');
  });

  async function handleFiles(files) {
    const validFiles = files.filter(f => /\.(csv|xlsx|xls|xlsm)$/i.test(f.name));
    if (validFiles.length === 0) { setStatus('CSVまたはExcelファイルを選択してください', 'error'); return; }
    setStatus(`${validFiles.length} 件を読み込み中...`, 'loading');

    let newPitches = [];
    for (const file of validFiles) {
      try {
        let parsed;
        if (/\.(xlsx|xls|xlsm)$/i.test(file.name)) {
          parsed = STATSCAST.parseExcel(await readFileAsArrayBuffer(file));
        } else {
          parsed = STATSCAST.parseCSV(await readFile(file));
        }
        newPitches = newPitches.concat(parsed);
      } catch (e) { console.error('ファイル読み込みエラー:', file.name, e); }
    }

    if (newPitches.length === 0) { setStatus('有効なデータが見つかりませんでした', 'error'); return; }

    if (allPitches.length > 0) {
      const merged = confirm(`既存データ（${allPitches.length} 球）があります。\n\nOK：追加合算\nキャンセル：置き換え`);
      allPitches = merged ? STATSCAST.mergeData(allPitches, newPitches) : newPitches;
    } else {
      allPitches = newPitches;
    }

    const ok = STATSCAST.saveData(allPitches);
    setStatus(ok
      ? `${allPitches.length} 球のデータを読み込みました`
      : `${allPitches.length} 球読み込み（容量上限のため未保存）`,
      ok ? 'success' : 'warning');
    showLists(allPitches);
  }

  function readFile(file) {
    return new Promise((res, rej) => {
      const r = new FileReader();
      r.onload = e => res(e.target.result);
      r.onerror = rej;
      r.readAsText(file, 'UTF-8');
    });
  }

  function readFileAsArrayBuffer(file) {
    return new Promise((res, rej) => {
      const r = new FileReader();
      r.onload = e => res(e.target.result);
      r.onerror = rej;
      r.readAsArrayBuffer(file);
    });
  }

  // --- 一覧表示 ---
  function showLists(pitches) {
    listSection.style.display = 'block';

    // 年度オプション
    const years = STATSCAST.getYears(pitches);
    const yearOpts = '<option value="">全年度</option>' +
      years.map(y => `<option value="${y}">${y}年</option>`).join('');
    yearFilter.innerHTML = yearOpts;
    pitcherYear.innerHTML = yearOpts;

    // 打者チームオプション
    const bTeams = STATSCAST.getTeams(pitches);
    teamFilter.innerHTML = '<option value="">全チーム</option>' +
      bTeams.map(t => `<option value="${escHtml(t)}">${escHtml(t)}</option>`).join('');

    // 投手チームオプション
    const pTeams = STATSCAST.getPitcherTeams(pitches);
    pitcherTeam.innerHTML = '<option value="">全チーム</option>' +
      pTeams.map(t => `<option value="${escHtml(t)}">${escHtml(t)}</option>`).join('');

    // sessionStorageから状態を復元
    searchInput.value   = SS.get('b_search');
    yearFilter.value    = SS.get('b_year');
    teamFilter.value    = SS.get('b_team');
    pitcherSearch.value = SS.get('p_search');
    pitcherYear.value   = SS.get('p_year');
    pitcherTeam.value   = SS.get('p_team');

    renderBatterTable(filterBatters(pitches));
    renderPitcherTable(filterPitchers(pitches));

    // イベントリスナー（毎回再設定）
    searchInput.oninput  = () => { SS.set('b_search', searchInput.value);  renderBatterTable(filterBatters(pitches)); };
    yearFilter.onchange  = () => { SS.set('b_year',   yearFilter.value);   renderBatterTable(filterBatters(pitches)); };
    teamFilter.onchange  = () => { SS.set('b_team',   teamFilter.value);   renderBatterTable(filterBatters(pitches)); };
    pitcherSearch.oninput = () => { SS.set('p_search', pitcherSearch.value); renderPitcherTable(filterPitchers(pitches)); };
    pitcherYear.onchange  = () => { SS.set('p_year',   pitcherYear.value);   renderPitcherTable(filterPitchers(pitches)); };
    pitcherTeam.onchange  = () => { SS.set('p_team',   pitcherTeam.value);   renderPitcherTable(filterPitchers(pitches)); };
  }

  function filterBatters(pitches) {
    const q    = searchInput.value.trim().toLowerCase();
    const year = yearFilter.value;
    const team = teamFilter.value;
    const filtered = pitches.filter(p =>
      (!year || String(p.Date || p.GameDate || '').includes(year)) &&
      (!team || p.BatterTeam === team)
    );
    let list = STATSCAST.getBatterList(filtered);
    if (q) list = list.filter(b => b.name.toLowerCase().includes(q));
    return list;
  }

  function filterPitchers(pitches) {
    const q    = pitcherSearch.value.trim().toLowerCase();
    const year = pitcherYear.value;
    const team = pitcherTeam.value;
    const filtered = pitches.filter(p =>
      (!year || String(p.Date || p.GameDate || '').includes(year)) &&
      (!team || p.PitcherTeam === team)
    );
    let list = STATSCAST.getPitcherList(filtered);
    if (q) list = list.filter(p => p.name.toLowerCase().includes(q));
    return list;
  }

  function renderBatterTable(batters) {
    const tbody = document.getElementById('batter-tbody');
    if (!batters.length) {
      tbody.innerHTML = '<tr><td colspan="7" style="text-align:center;color:#888">該当選手なし</td></tr>';
      return;
    }
    tbody.innerHTML = batters.map(b => {
      const ba = b.ab > 0 ? (b.hits / b.ab).toFixed(3).replace(/^0/, '') : '—';
      return `<tr>
        <td><a href="batter.html?name=${encodeURIComponent(b.name)}">${escHtml(b.name)}</a></td>
        <td>${escHtml(b.team)}</td>
        <td class="num">${b.ab}</td>
        <td class="num">${b.hits}</td>
        <td class="num">${ba}</td>
        <td class="num">${b.k}</td>
        <td class="num">${b.bb}</td>
      </tr>`;
    }).join('');
  }

  function renderPitcherTable(pitchers) {
    const tbody = document.getElementById('pitcher-tbody');
    if (!pitchers.length) {
      tbody.innerHTML = '<tr><td colspan="7" style="text-align:center;color:#888">該当投手なし</td></tr>';
      return;
    }
    tbody.innerHTML = pitchers.map(p => {
      const ba = p.ab > 0 ? (p.hits / p.ab).toFixed(3).replace(/^0/, '') : '—';
      return `<tr>
        <td><a href="pitcher.html?name=${encodeURIComponent(p.name)}">${escHtml(p.name)}</a></td>
        <td>${escHtml(p.team)}</td>
        <td class="num">${p.ab}</td>
        <td class="num">${p.hits}</td>
        <td class="num">${ba}</td>
        <td class="num">${p.k}</td>
        <td class="num">${p.bb}</td>
      </tr>`;
    }).join('');
  }

  function setStatus(msg, type) {
    dataStatus.textContent = msg;
    dataStatus.className = 'data-status ' + (type || '');
  }

  function escHtml(s) {
    return String(s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;')
      .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  }
})();
