// ECHO Documentation - Navigation Engine
document.addEventListener('DOMContentLoaded', () => {
    const sidebar = document.querySelector('.sidebar');
    if (!sidebar) return;

    // Get current page filename
    const currentPage = window.location.pathname.split('/').pop() || 'index.html';

    const navItems = [
        { href: 'index.html', text: 'Introduction' },
        { href: '00_fondations.html', text: '0. Fondations & Philosophie' },
        { href: '01_hld_architecture.html', text: '1. High-Level Design (HLD)' },
        { href: '02_deploiement.html', text: '2. Déploiement & Infra' },
        { href: '03_echo_libs.html', text: '3. Librairies Partagées (libs)' },
        { href: '04_hud_ui.html', text: '4. Écosystème HUD & UI' },
        { href: '05_filtre.html', text: '5. Le Filtre (Conscience)' },
        { href: '06_pipe.html', text: '6. Le Pipe (Cortex)' },
        { 
            href: '07_arsenal_outils.html', 
            text: '7. L\'Arsenal des Outils',
            sub: [
                { href: '07a_web_intelligence.html', text: '7a. Web Intelligence' },
                { href: '07b_vault_explorer.html', text: '7b. Vault & Data Explorer' },
                { href: '07c_memory_cognition.html', text: '7c. Mémoire & Cognition' },
                { href: '07d_execution_monitoring.html', text: '7d. Exécution & Pilotage' },
                { href: '07e_actions_ui.html', text: '7e. Actions UI (HUD)' }
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
        <nav>
            <ul>`;

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

    html += `</ul></nav>`;
    sidebar.innerHTML = html;
});