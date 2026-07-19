/* HatBhau — shared frontend helpers */

// ── Inline placeholder SVG as data URI (no file needed) ──────────────
const PLACEHOLDER_SVG = `data:image/svg+xml,${encodeURIComponent(`
<svg xmlns="http://www.w3.org/2000/svg" width="200" height="200" viewBox="0 0 200 200">
  <rect width="200" height="200" fill="#e8ecf1"/>
  <rect x="70" y="50" width="60" height="60" rx="8" fill="#d0d4d9"/>
  <circle cx="100" cy="80" r="15" fill="#b8bcc1"/>
  <rect x="50" y="130" width="100" height="20" rx="4" fill="#d0d4d9"/>
  <rect x="60" y="158" width="80" height="14" rx="4" fill="#d0d4d9"/>
  <text x="100" y="195" text-anchor="middle" font-family="Arial" font-size="12" fill="#999">No Image</text>
</svg>
`)}`;

// ── Product card with inline placeholder ──────────────────────────────
function productCard(p) {
    const discountTag = p.discount > 0 ? `<div class="discount-tag">-${Math.round(p.discount)}%</div>` : '';
    const price = p.price || 0;
    const originalPrice = p.original_price || p.originalPrice || 0;
    
    // Use inline placeholder if image is missing or invalid
    let imageUrl = p.image_url || '';
    if (!imageUrl || imageUrl === 'None' || imageUrl === 'null' || imageUrl === 'undefined' || imageUrl === '') {
        imageUrl = PLACEHOLDER_SVG;
    }
    // If URL is relative, prepend slash
    if (imageUrl && !imageUrl.startsWith('http') && !imageUrl.startsWith('/') && !imageUrl.startsWith('data:')) {
        imageUrl = '/' + imageUrl;
    }
    
    const storeName = p.store || p.source || 'Store';
    const storeIcon = getStoreIcon(storeName);
    
    return `
    <div class="col-6 col-md-4 col-xl-3">
        <div class="product-card glass-card h-100">
            <a href="/product/${p.id}" class="text-decoration-none d-block">
                <div class="product-img-wrapper">
                    ${discountTag}
                    <div class="store-badge-card">
                        <i class="bi ${storeIcon} me-1"></i>${storeName}
                    </div>
                    <img src="${imageUrl}" class="product-img"
                         alt="${p.name || 'Product'}" loading="lazy"
                         onerror="this.onerror=null; this.src='${PLACEHOLDER_SVG}';">
                </div>
                <div class="product-card-body">
                    <div class="product-title-link">${p.name || 'Unnamed Product'}</div>
                    <div class="product-price-row">
                        <span class="fw-800 text-accent">Rs. ${price.toLocaleString()}</span>
                        ${originalPrice ? `<span class="orig-price ms-2">Rs. ${originalPrice.toLocaleString()}</span>` : ''}
                    </div>
                </div>
            </a>
        </div>
    </div>`;
}

// ── Store icon mapping ────────────────────────────────────────────────
function getStoreIcon(storeName) {
    const icons = {
        'Daraz': 'bi-bag-check',
        'Brother Mart': 'bi-shop2', 
        'PriceOye': 'bi-globe',
        'Sinja': 'bi-shop',
        '91mobiles': 'bi-phone',
        'Fatafat Sewa': 'bi-lightning-charge',
        'GadgetByte Nepal': 'bi-cpu',
    };
    // Find matching icon (case-insensitive)
    const lowerName = storeName.toLowerCase();
    for (const [key, icon] of Object.entries(icons)) {
        if (lowerName.includes(key.toLowerCase())) {
            return icon;
        }
    }
    return 'bi-shop';
}

// ── Theme toggle ──────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
    const root = document.documentElement;
    const toggle = document.getElementById('theme-toggle');
    const saved = localStorage.getItem('hatbhau-theme');
    if (saved) root.setAttribute('data-theme', saved);

    toggle?.addEventListener('click', () => {
        const next = root.getAttribute('data-theme') === 'dark' ? 'light' : 'dark';
        root.setAttribute('data-theme', next);
        localStorage.setItem('hatbhau-theme', next);
        const icon = toggle.querySelector('i');
        if (icon) icon.className = next === 'dark' ? 'bi bi-moon-stars-fill' : 'bi bi-sun-fill';
    });

    setupSearchSuggestions('nav-search-input', 'nav-suggestions');
    setupSearchSuggestions('hero-search-input', 'hero-suggestions');
});

// ── Debounced search suggestions ─────────────────────────────────────
function setupSearchSuggestions(inputId, dropdownId) {
    const input = document.getElementById(inputId);
    const dropdown = document.getElementById(dropdownId);
    if (!input || !dropdown) return;

    let debounceTimer;
    input.addEventListener('input', () => {
        clearTimeout(debounceTimer);
        const q = input.value.trim();
        if (q.length < 2) { 
            dropdown.innerHTML = ''; 
            dropdown.classList.remove('show'); 
            return; 
        }
        debounceTimer = setTimeout(() => {
            fetch(`/api/search?q=${encodeURIComponent(q)}&per_page=5`)
                .then(r => r.json())
                .then(data => {
                    if (!data.items || data.items.length === 0) {
                        dropdown.innerHTML = '<div class="p-2 text-muted small">No matches</div>';
                    } else {
                        dropdown.innerHTML = data.items.map(p => `
                            <a href="/product/${p.id}" class="suggestion-item d-flex align-items-center gap-2 p-2 text-decoration-none">
                                <img src="${p.image_url || PLACEHOLDER_SVG}" width="32" height="32" class="rounded" style="object-fit:cover;"
                                     onerror="this.onerror=null; this.src='${PLACEHOLDER_SVG}';">
                                <span class="text-truncate small">${p.name || 'Product'}</span>
                                <span class="ms-auto small text-accent">Rs. ${(p.price || 0).toLocaleString()}</span>
                            </a>`).join('');
                    }
                    dropdown.classList.add('show');
                })
                .catch(() => { 
                    dropdown.innerHTML = ''; 
                    dropdown.classList.remove('show'); 
                });
        }, 250);
    });

    document.addEventListener('click', (e) => {
        if (!input.contains(e.target) && !dropdown.contains(e.target)) {
            dropdown.classList.remove('show');
        }
    });
}

// ── Compare product card ──────────────────────────────────────────────
function compareProductCard(p, isCheapest = false) {
    const price = p.price || 0;
    const discount = p.discount || 0;
    const storeName = p.store || p.source || 'Store';
    const storeIcon = getStoreIcon(storeName);
    let imageUrl = p.image_url || '';
    if (!imageUrl || imageUrl === 'None' || imageUrl === 'null' || imageUrl === 'undefined' || imageUrl === '') {
        imageUrl = PLACEHOLDER_SVG;
    }
    
    return `
    <div class="compare-store-card ${isCheapest ? 'cheapest-store' : ''}" onclick="viewProduct(${p.id})">
        <div class="store-header">
            <div class="store-info">
                <i class="bi ${storeIcon} store-icon"></i>
                <span class="store-name">${storeName}</span>
            </div>
            ${isCheapest ? '<span class="badge-best"><i class="bi bi-trophy-fill"></i> Best Price</span>' : ''}
        </div>
        <div class="product-info">
            <div class="product-image-compare">
                <img src="${imageUrl}" alt="${p.name || 'Product'}" 
                     onerror="this.onerror=null; this.src='${PLACEHOLDER_SVG}';">
            </div>
            <div class="product-name-compare">${p.name || 'Unnamed Product'}</div>
            <div class="price-section">
                <span class="price ${isCheapest ? 'lowest' : ''}">Rs. ${price.toLocaleString()}</span>
                ${discount > 0 ? `<span class="discount-badge">-${Math.round(discount)}%</span>` : ''}
            </div>
            ${p.rating ? `<div class="rating"><i class="bi bi-star-fill"></i> ${p.rating}</div>` : ''}
            <div class="availability ${p.availability === 'In Stock' ? 'in-stock' : 'out-of-stock'}">
                <i class="bi ${p.availability === 'In Stock' ? 'bi-check-circle-fill' : 'bi-x-circle-fill'}"></i>
                ${p.availability || 'In Stock'}
            </div>
        </div>
    </div>`;
}