/**
 * ECHO Documentation - nav.js v2.1
 * Génération sidebar + TOC dynamique imbriqué.
 * Aucun style inline - pilotage exclusif par classes CSS (style.css).
 */
document.addEventListener('DOMContentLoaded', () => {

  /* ============================================================
     0. MOBILE HEADER & OVERLAY
     ============================================================ */
  const mobileHeader = document.createElement('div');
  mobileHeader.className = 'mobile-header';
  mobileHeader.innerHTML = '<button class="menu-toggle">☰</button><span>ECHO v5 Docs</span>';
  document.body.prepend(mobileHeader);

  const sidebarOverlay = document.createElement('div');
  sidebarOverlay.className = 'sidebar-overlay';
  document.body.appendChild(sidebarOverlay);

  const menuBtn = mobileHeader.querySelector('.menu-toggle');
  menuBtn.addEventListener('click', () => {
    document.querySelector('.sidebar')?.classList.add('open');
    sidebarOverlay.classList.add('active');
  });

  sidebarOverlay.addEventListener('click', () => {
    document.querySelector('.sidebar')?.classList.remove('open');
    sidebarOverlay.classList.remove('active');
  });

  /* ============================================================
     1. GÉNÉRATION DE LA SIDEBAR
     ============================================================ */
  const sidebar = document.querySelector('.sidebar');
  if (sidebar) {
    const currentPage = window.location.pathname.split('/').pop() || 'index.html';
    const navItems = [
      { href: 'index.html',              text: 'Introduction' },
      { href: '00_fondations.html',      text: '0. Fondations & Philosophie' },
      { href: '01_hld_architecture.html',text: '1. High-Level Design (HLD)' },
      { href: '02_communication_gemini.html', text: '2. Communication Gemini' },
      { href: '03_deploiement.html',     text: '3. Déploiement & Infra' },
      { href: '04_echo_libs.html',       text: '4. Librairies Partagées' },
      { href: '05_hud_ui.html',          text: '5. Écosystème HUD & UI' },
      { href: '06_filtre.html',          text: '6. Les Filtres (Conscience)' },
      { href: '07_pipe.html',            text: '7. Le Pipe (Cortex)' },
      {
        href: '08_arsenal_outils.html',
        text: "8. L'Arsenal des Outils",
        sub: [
          { href: '08a_strategic_planner.html', text: '8a. Planification Stratégique' },
          { href: '08b_web_intelligence.html',  text: '8b. Web Intelligence' },
          { href: '08c_vault_explorer.html',    text: "8c. Explorateur de l'Espace Personnel" },
          { href: '08d_memory_cognition.html',  text: '8d. Mémoire & Cognition' },
          { href: '08e_execution_monitoring.html', text: '8e. Exécution & Pilotage' },
          { href: '08f_actions_ui.html',        text: '8f. Actions UI (HUD)' },
          { href: '08g_visual_intelligence.html', text: '8g. Visual Intelligence' },
          { href: '08h_codex_editor.html',      text: '8h. ECHO Codex (Éditeur)' },
          { href: '08i_delegate_agent.html', text: '8i. Delegate Agent' },
          { href: '08j_agent_orchestration.html', text: '8j. Orchestration Multi-Agents' },
          { href: '08k_n8n_orchestrator.html', text: '8k. Orchestrateur N8N' },
          { href: '08l_mcp_broker.html', text: '8l. Serveur MCP Broker' }
        ]
      },
      { href: '09_system_prompt.html',   text: '9. Le Kernel (System Prompt)' },
      {
        href: '10_infrastructure.html',
        text: '10. Périphériques & Infra',
        sub: [
          { href: '10a_admin_manager.html', text: '10a. Admin Manager' },
          { href: '10b_echo_auth_sso.html', text: '10b. ECHO Auth SSO & MFA' },
          { href: '10c_bunkerweb_waf.html', text: '10c. Bouclier BunkerWeb WAF' },
          { href: '10d_audio_workers.html', text: '10d. Audio Workers' },
          { href: '10e_scripts_infrastructure.html', text: '10e. Scripts d\'Infrastructure' },
          { href: '10f_download_broker.html', text: '10f. Download Broker' }
        ]
      },
      {
        href: '11_edge_inference.html',
        text: '11. Inférence Distante (Edge Computing)'
      },
      {
        href: '12_annexes.html',
        text: '12. Annexes Techniques'
      },
      {
        href: '13_credits.html',
        text: '13. Crédits Open Source'
      },
      {
        href: '14_manuel_utilisateur.html',
        text: '14. Manuel Utilisateur'
      },
      {
        href: '15_registre_audit.html',
        text: "15. Registre d'Audit et Confidentialité"
      }
    ];

    let html = `
      <div class="logo-container">
        <img src="logo-echo-medium.png" alt="ECHO Logo">
      </div>
      <h2>ECHO v5</h2>
      <div class="valve-container notranslate">
        <div class="valve-toggle" id="lang-toggle">
          <div class="valve-slider" id="valve-slider"></div>
          <div class="valve-option active" id="opt-fr">FR</div>
          <div class="valve-option" id="opt-en">EN</div>
        </div>
      </div>
      <div id="google_translate_element" style="display:none;"></div>
      <nav><ul>`;

    navItems.forEach(item => {
      const isActive    = currentPage === item.href;
      const isSubActive = item.sub && item.sub.some(s => s.href === currentPage);
      const showSub     = isActive || isSubActive;

      html += `<li><a href="${item.href}" class="${isActive ? 'active' : ''}">${item.text}</a>`;
      if (item.sub) {
        // Classe CSS 'hidden' au lieu de style="display:none"
        html += `<ul class="sub-nav${showSub ? '' : ' hidden'}">`;
        item.sub.forEach(subItem => {
          const isSubItemActive = currentPage === subItem.href;
          html += `<li><a href="${subItem.href}" class="${isSubItemActive ? 'active' : ''}">${subItem.text}</a></li>`;
        });
        html += `</ul>`;
      }
      html += `</li>`;
    });

    sidebar.innerHTML = html + `</ul></nav>`;
  }

  /* ============================================================
     2. GÉNÉRATION DU SOMMAIRE DYNAMIQUE IMBRIQUÉ (TOC)
     ============================================================ */
  const main = document.querySelector('main');
  if (!main) return;

  // Collecte tous les h2 et h3 du contenu principal
  const headings = Array.from(main.querySelectorAll('h2, h3'));
  if (headings.length < 2) return; // Pas de TOC si moins de 2 titres

  // Injection de la div TOC dans le DOM
  const tocEl = document.createElement('nav');
  tocEl.id = 'page-toc';
  tocEl.setAttribute('aria-label', 'Sommaire');

  // Slug URL-safe depuis le texte
  const toSlug = (text) =>
    text.toLowerCase()
        .normalize('NFD').replace(/[\u0300-\u036f]/g, '') // Suppression accents
        .replace(/[^a-z0-9\s-]/g, '')
        .trim().replace(/\s+/g, '-');

  let tocHTML = '<h3>Sommaire</h3><ol>';
  let currentH2Li = null;
  let currentSubList = null;
  let h2Count = 0;

  headings.forEach(heading => {
    // Ajout d'un id si absent
    if (!heading.id) {
      heading.id = toSlug(heading.textContent);
    }
    const text = heading.textContent.trim();
    const id   = heading.id;

    if (heading.tagName === 'H2') {
      h2Count++;
      if (currentSubList) tocHTML += '</ol></li>';
      tocHTML += `<li><a href="#${id}">${text}</a>`;
      currentSubList = null;
    } else if (heading.tagName === 'H3') {
      if (!currentSubList) {
        tocHTML += '<ol class="toc-sub">';
        currentSubList = true;
      }
      tocHTML += `<li><a href="#${id}">${text}</a></li>`;
    }
  });
  if (currentSubList) tocHTML += '</ol>';
  tocHTML += '</li></ol>';

  tocEl.innerHTML = tocHTML;
  document.body.appendChild(tocEl);

  /* ---- Surlignage de la section active au scroll ---- */
  const tocLinks = tocEl.querySelectorAll('a');
  const headingEls = headings;

  const observer = new IntersectionObserver(
    (entries) => {
      entries.forEach(entry => {
        if (entry.isIntersecting) {
          tocLinks.forEach(l => l.classList.remove('toc-active'));
          const active = tocEl.querySelector(`a[href="#${entry.target.id}"]`);
          if (active) active.classList.add('toc-active');
        }
      });
    },
    { rootMargin: '-10% 0px -80% 0px', threshold: 0 }
  );
  headingEls.forEach(h => observer.observe(h));

  /* ============================================================
     3. MOTEUR ZOOM UNIVERSEL (Mermaid + SVG + Images)
     ============================================================ */
  const modal = document.createElement('div');
  modal.className = 'modal-overlay';
  modal.innerHTML = `
    <div class="zoom-control">
      <label id="zoom-label">140%</label>
      <input type="range" id="zoom-slider" min="50" max="300" value="140">
    </div>
    <span class="modal-close">&times;</span>
    <div class="modal-content"><div id="modal-svg-container"></div></div>`;
  document.body.appendChild(modal);

  const modalContent     = modal.querySelector('.modal-content');
  const modalSvgContainer = modal.querySelector('#modal-svg-container');
  const closeBtn         = modal.querySelector('.modal-close');
  const zoomSlider       = modal.querySelector('#zoom-slider');
  const zoomLabel        = modal.querySelector('#zoom-label');

  let isDragging = false;
  let startX, startY, scrollLeft, scrollTop;

  const updateZoom = (val) => {
    const factor = val / 100;
    zoomLabel.innerText = val + '%';
    const target = modalSvgContainer.querySelector('svg, img');
    if (!target) return;
    const cx = (modal.scrollLeft + window.innerWidth  / 2) / modalContent.offsetWidth;
    const cy = (modal.scrollTop  + window.innerHeight / 2) / modalContent.offsetHeight;
    target.style.transform = `scale(${factor})`;
    setTimeout(() => {
      modal.scrollLeft = cx * modalContent.offsetWidth  - window.innerWidth  / 2;
      modal.scrollTop  = cy * modalContent.offsetHeight - window.innerHeight / 2;
    }, 0);
  };

  const openModal = (element) => {
    let content = '';
    if (element.tagName === 'svg' || element.querySelector('svg')) {
      const svg = (element.tagName === 'svg' ? element : element.querySelector('svg')).cloneNode(true);
      svg.removeAttribute('width');
      svg.removeAttribute('height');
      svg.style.width  = '80vw';
      svg.style.height = 'auto';
      content = svg.outerHTML;
    } else if (element.tagName === 'IMG') {
      content = `<img src="${element.src}" style="width:80vw;height:auto;" alt="">`;
    }
    if (!content) return;
    modalSvgContainer.innerHTML = content;
    modal.style.display = 'block';
    document.body.style.overflow = 'hidden';
    zoomSlider.value = 140;
    updateZoom(140);
    setTimeout(() => {
      modal.scrollLeft = (modalContent.offsetWidth  - window.innerWidth)  / 2;
      modal.scrollTop  = (modalContent.offsetHeight - window.innerHeight) / 2;
    }, 10);
  };

  const closeModal = () => {
    modal.style.display = 'none';
    document.body.style.overflow = '';
    modalSvgContainer.innerHTML = '';
  };

  zoomSlider.addEventListener('input', (e) => updateZoom(e.target.value));

  // Drag-to-pan
  modal.addEventListener('mousedown', (e) => {
    if (e.target.closest('.modal-close') || e.target.closest('.zoom-control')) return;
    isDragging = true;
    startX = e.pageX - modal.offsetLeft;
    startY = e.pageY - modal.offsetTop;
    scrollLeft = modal.scrollLeft;
    scrollTop  = modal.scrollTop;
  });
  modal.addEventListener('mouseleave', () => isDragging = false);
  modal.addEventListener('mouseup',    () => isDragging = false);
  modal.addEventListener('mousemove',  (e) => {
    if (!isDragging) return;
    e.preventDefault();
    const x = e.pageX - modal.offsetLeft;
    const y = e.pageY - modal.offsetTop;
    modal.scrollLeft = scrollLeft - (x - startX);
    modal.scrollTop  = scrollTop  - (y - startY);
  });

  // Clic pour ouvrir (mermaid-wrapper, svg-diagram, images)
  document.addEventListener('click', (e) => {
    if (modal.style.display === 'block') return;
    const wrapper = e.target.closest('.mermaid-wrapper, .svg-diagram');
    if (wrapper) { openModal(wrapper); return; }
    if (e.target.tagName === 'IMG' && !e.target.closest('.sidebar')) openModal(e.target);
  });

  closeBtn.addEventListener('click', (e) => { e.stopPropagation(); closeModal(); });
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape') closeModal(); });

  /* ============================================================
     4. MOTEUR DE TRADUCTION (Google Translate)
     ============================================================ */
  const gtScript = document.createElement('script');
  gtScript.type = 'text/javascript';
  // URL absolue obligatoire (https:) pour éviter les échecs sous file:///
  gtScript.src = 'https://translate.google.com/translate_a/element.js?cb=googleTranslateElementInit';
  document.head.appendChild(gtScript);

  window.googleTranslateElementInit = function() {
    new google.translate.TranslateElement({
      pageLanguage: 'fr', 
      includedLanguages: 'en,fr', 
      autoDisplay: false
    }, 'google_translate_element');
  };

  const langToggle = document.getElementById('lang-toggle');
  const optFr = document.getElementById('opt-fr');
  const optEn = document.getElementById('opt-en');

  if (langToggle) {
    const isEn = document.cookie.includes('googtrans=/fr/en') || window.location.hash.includes('googtrans');
    if (isEn) {
      langToggle.classList.add('is-en');
      optFr.classList.remove('active');
      optEn.classList.add('active');
    }

    langToggle.addEventListener('click', () => {
      const currentlyEn = langToggle.classList.contains('is-en');
      if (!currentlyEn) {
        // Switch to EN
        document.cookie = "googtrans=/fr/en; path=/";
        if (location.hostname) {
          document.cookie = "googtrans=/fr/en; domain=" + location.hostname + "; path=/";
        }
        window.location.hash = "#googtrans(fr|en)";
        window.location.reload();
      } else {
        // Switch to FR
        document.cookie = "googtrans=; expires=Thu, 01 Jan 1970 00:00:00 UTC; path=/;";
        document.cookie = "googtrans=; expires=Thu, 01 Jan 1970 00:00:00 UTC;";
        if (location.hostname) {
          const hostParts = location.hostname.split('.');
          for (let i = 0; i < hostParts.length; i++) {
            const domain = hostParts.slice(i).join('.');
            document.cookie = "googtrans=; expires=Thu, 01 Jan 1970 00:00:00 UTC; domain=" + domain + "; path=/;";
            document.cookie = "googtrans=; expires=Thu, 01 Jan 1970 00:00:00 UTC; domain=." + domain + "; path=/;";
          }
        }
        // Force URL without hash to clear Google Translate state completely
        window.history.replaceState({}, document.title, window.location.pathname + window.location.search);
        window.location.reload();
      }
    });
  }
});
