'use strict';

(async () => {
  let allPitches = [];

  const dataStatus   = document.getElementById('data-status');
  const uploadArea   = document.getElementById('upload-area');
  const csvInput     = document.getElementById('csv-input');
  const batterSection = document.getElementById('batter-list-section');
  const searchInput  = document.getElementById('search-input');
  const teamFilter   = document.getElementById('team-filter');
  const clearBtn     = document.getElementById('clear-data-btn');

  // data/data.csv から自動読み込みを試みる
  setStatus('データを確認中...', 'loading');
  const filePitches = await STATSCAST.tryLoadFromFile();
  if (filePitches && filePitches.length > 0) {
    allPitches = filePitches;
    STATSCAST.saveData(allPitches);
    setStatus(`data/data.csv から ${allPitches.length} 球を読み込みました`, 'success');
    showBatterList(allPitches);
  } else {
    allPitches = STATSCAST.loadStoredData();
    if (allPitches.length > 0) {
      setStatus(`保存済みデータ ${allPitches.length} 球を読み込みました`, 'success');
      showBatterList(allPitches);
    } else {
      setStatus('CSVファイルをアップロードしてください', '');
    }
  }

  // --- ファイルアップロード ---
  uploadArea.addEventListener('click', () => csvInput.click());

  uploadArea.addEventListener('dragover', e => {
    e.preventDefault();
    uploadArea.classList.add('dragover');
  });
  uploadArea.addEventListener('dragleave', () => uploadArea.classList.remove('dragover'));
  uploadArea.addEventListener('drop', e => {
    e.preventDefault();
    uploadArea.classList.remove('dragover');
    handleFiles(Array.from(e.dataTransfer.files));
  });

  csvInput.addEventListener('change', e => {
    handleFiles(Array.from(e.target.files));
    e.target.value = '';
  });

  clearBtn.addEventListener('click', () => {
    if (!confirm('保存済みデータを削除しますか？')) return;
    STATSCAST.clearData();
    allPitches = [];
    batterSection.style.display = 'none';
    setStatus('データを削除しました', '');
  });

  async function handleFiles(files) {
    const csvFiles = files.filter(f => /\.(csv)$/i.test(f.name));
    if (csvFiles.length === 0) {
      setStatus('CSVファイルを選択してください', 'error');
      return;
    }
    setStatus(`${csvFiles.length} 件を読み込み中...`, 'loading');

    let newPitches = [];
    for (const file of csvFiles) {
      try {
        const text = await readFile(file);
        const parsed = STATSCAST.parseCSV(text);
        newPitches = newPitches.concat(parsed);
      } catch (e) {
        console.error('ファイル読み込みエラー:', file.name, e);
      }
    }

    if (newPitches.length === 0) {
      setStatus('有効なデータが見つかりませんでした', 'error');
      return;
    }

    if (allPitches.length > 0) {
      const merged = confirm(
        `既存データ（${allPitches.length} 球）があります。\n\nOK：追加合算\nキャンセル：置き換え`
      );
      allPitches = merged
        ? STATSCAST.mergeData(allPitches, newPitches)
        : newPitches;
    } else {
      allPitches = newPitches;
    }

    const ok = STATSCAST.saveData(allPitches);
    if (!ok) {
      setStatus(`${allPitches.length} 球読み込み（容量上限のため未保存）`, 'warning');
    } else {
      setStatus(`${allPitches.length} 球のデータを読み込みました`, 'success');
    }
    showBatterList(allPitches);
  }

  function readFile(file) {
    return new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = e => resolve(e.target.result);
      reader.onerror = reject;
      reader.readAsText(file, 'UTF-8');
    });
  }

  function showBatterList(pitches) {
    batterSection.style.display = 'block';

    const teams = STATSCAST.getTeams(pitches);
    teamFilter.innerHTML =
      '<option value="">全チーム</option>' +
      teams.map(t => `<option value="${escHtml(t)}">${escHtml(t)}</option>`).join('');

    renderTable(STATSCAST.getBatterList(pitches));
    searchInput.oninput = () => filterAndRender(pitches);
    teamFilter.onchange = () => filterAndRender(pitches);
  }

  function filterAndRender(pitches) {
    const q    = searchInput.value.trim().toLowerCase();
    const team = teamFilter.value;
    const filtered = pitches.filter(p =>
      (!team || p.BatterTeam === team)
    );
    let list = STATSCAST.getBatterList(filtered);
    if (q) list = list.filter(b => b.name.toLowerCase().includes(q));
    renderTable(list);
  }

  function renderTable(batters) {
    const tbody = document.getElementById('batter-tbody');
    if (batters.length === 0) {
      tbody.innerHTML = '<tr><td colspan="6" style="text-align:center;color:#888">該当選手なし</td></tr>';
      return;
    }
    tbody.innerHTML = batters.map(b => {
      const ba = b.ab > 0
        ? (b.hits / b.ab).toFixed(3).replace(/^0/, '')
        : '—';
      return `
        <tr>
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

  function setStatus(msg, type) {
    dataStatus.textContent = msg;
    dataStatus.className = 'data-status ' + (type || '');
  }

  function escHtml(s) {
    return String(s)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }
})();
