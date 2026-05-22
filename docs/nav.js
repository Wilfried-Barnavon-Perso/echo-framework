// ECHO Documentation - Navigation Engine & Diagram Zoom
document.addEventListener('DOMContentLoaded', () => {
  /* 1. SIDEBAR GENERATION */
  const sidebar = document.querySelector('.sidebar');
  if (sidebar) {
    const currentPage = window.location.pathname.split('/').pop() || 'index.html';
    const navItems = [
      { href: 'index.html', text: 'Introduction' },
      { href: '00_fondations.html', text: '0. Fondations & Philosophie' },
      { href: '01_hld_architecture.html', text: '1. High-Level Design (HLD)' },
      { href: '02_deploiement.html', text: '2. Déploiement & Infra' },
      { href: '03_echo_libs.html', text: '3. Librairies Partagées (libs)' },
      { href: '04_hud_ui.html', text: '4. Écosystème HUD & UI' },
      { href: '05_filtre.html', text: '5. Les Filtres (Conscience & Mémoire)' },
      { href: '06_pipe.html', text: '6. Le Pipe (Cortex)' },
      {
        href: '07_arsenal_outils.html',
        text: "7. L'Arsenal des Outils",
        sub: [
          { href: '07a_web_intelligence.html', text: '7a. Web Intelligence' },
          { href: '07b_vault_explorer.html', text: "7b. Explorateur de l'Espace Personnel" },
          { href: '07c_memory_cognition.html', text: '7c. Mémoire & Cognition' },
          { href: '07d_execution_monitoring.html', text: '7d. Exécution & Pilotage' },
          { href: '07e_actions_ui.html', text: '7e. Actions UI (HUD)' },
          { href: '07f_visual_intelligence.html', text: '7f. Visual Intelligence (Rendu Visuel)' }
        ]
      },
      { href: '08_system_prompt.html', text: '8. Le Kernel (System Prompt)' },
      { href: '09_admin_manager.html', text: '9. Admin Manager' },
      { href: '10_annexes.html', text: '10. Annexes Techniques' }
    ];

    let html = `
      <div class="logo-container">
        <img src="logo-echo.png" alt="ECHO Logo">
      </div>
      <h2>ECHO v5</h2>
      <nav><ul>`;

    navItems.forEach(item => {
      const isActive = currentPage === item.href;
      const isSubActive = item.sub && item.sub.some(s => s.href === currentPage);
      html += `<li><a href="${item.href}" class="${isActive ? 'active' : ''}">${item.text}</a>`;
      if (item.sub) {
        html += `<ul class="sub-nav" style="display: ${isActive || isSubActive ? 'block' : 'none'}">`;
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

  /* 2. UNIVERSAL ZOOM ENGINE (Centred Zoom + Drag) */
  const modal = document.createElement('div');
  modal.className = 'modal-overlay';
  modal.innerHTML = `
    <div class="zoom-control">
      <label id="zoom-label">140%</label>
      <input type="range" id="zoom-slider" min="50" max="200" value="140">
    </div>
    <span class="modal-close">&times;</span>
    <div class="modal-content"><div id="modal-svg-container"></div></div>`;
  document.body.appendChild(modal);

  const modalContent = modal.querySelector('.modal-content');
  const modalSvgContainer = modal.querySelector('#modal-svg-container');
  const closeBtn = modal.querySelector('.modal-close');
  const zoomSlider = modal.querySelector('#zoom-slider');
  const zoomLabel = modal.querySelector('#zoom-label');

  let isDragging = false;
  let startX, startY, scrollLeft, scrollTop;

  const updateZoom = (val) => {
    const factor = val / 100;
    zoomLabel.innerText = val + "%";
    
    // Cible (SVG ou IMG)
    const target = modalSvgContainer.querySelector('svg, img');
    if (!target) return;

    // On sauvegarde le centre relatif AVANT le zoom
    const relativeCenterX = (modal.scrollLeft + window.innerWidth / 2) / modalContent.offsetWidth;
    const relativeCenterY = (modal.scrollTop + window.innerHeight / 2) / modalContent.offsetHeight;

    // Application de l'échelle
    target.style.transform = `scale(${factor})`;
    
    // On force le rafraîchissement du scroll pour rester centré
    setTimeout(() => {
      modal.scrollLeft = (relativeCenterX * modalContent.offsetWidth) - (window.innerWidth / 2);
      modal.scrollTop = (relativeCenterY * modalContent.offsetHeight) - (window.innerHeight / 2);
    }, 0);
  };

  const openModal = (element) => {
    let content = "";
    if (element.tagName === 'svg' || element.querySelector('svg')) {
      const svg = element.tagName === 'svg' ? element.cloneNode(true) : element.querySelector('svg').cloneNode(true);
      svg.removeAttribute('width'); svg.removeAttribute('height');
      svg.style.width = "80vw"; // Taille de base stable
      content = svg.outerHTML;
    } else if (element.tagName === 'IMG') {
      content = `<img src="${element.src}" style="width:80vw;">`;
    }

    modalSvgContainer.innerHTML = content;
    modal.style.display = 'block';
    document.body.style.overflow = 'hidden';
    
    zoomSlider.value = 140;
    updateZoom(140);

    // Centrage initial
    setTimeout(() => {
      modal.scrollLeft = (modalContent.offsetWidth - window.innerWidth) / 2;
      modal.scrollTop = (modalContent.offsetHeight - window.innerHeight) / 2;
    }, 10);
  };

  const closeModal = () => {
    modal.style.display = 'none';
    document.body.style.overflow = 'auto';
    modalSvgContainer.innerHTML = '';
  };

  zoomSlider.addEventListener('input', (e) => updateZoom(e.target.value));

  // Drag Logic
  modal.addEventListener('mousedown', (e) => {
    if (e.target.closest('.modal-close') || e.target.closest('.zoom-control')) return;
    isDragging = true;
    startX = e.pageX - modal.offsetLeft;
    startY = e.pageY - modal.offsetTop;
    scrollLeft = modal.scrollLeft;
    scrollTop = modal.scrollTop;
  });

  modal.addEventListener('mouseleave', () => isDragging = false);
  modal.addEventListener('mouseup', () => isDragging = false);
  modal.addEventListener('mousemove', (e) => {
    if (!isDragging) return;
    e.preventDefault();
    const x = e.pageX - modal.offsetLeft;
    const y = e.pageY - modal.offsetTop;
    modal.scrollLeft = scrollLeft - (x - startX);
    modal.scrollTop = scrollTop - (y - startY);
  });

  document.addEventListener('click', (e) => {
    if (modal.style.display !== 'block') {
      const wrapper = e.target.closest('.mermaid-wrapper');
      if (wrapper) openModal(wrapper);
      else if (e.target.tagName === 'IMG' && !e.target.closest('.sidebar')) openModal(e.target);
    }
  });

  closeBtn.addEventListener('click', (e) => { e.stopPropagation(); closeModal(); });
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape') closeModal(); });
});
