/**
 * Verve Minimal Photo Platform — Client Interactions & Application Controller
 */

document.addEventListener('DOMContentLoaded', () => {
  initThemeToggle();
  initCarousels();
  initDoubleTapLike();
  initSocialActionButtons();
  initInlineComments();
  initCreatePostWizard();
  initProfileTabs();
  initQuickLightbox();
});

/* ==========================================================================
   1. Theme Manager
   ========================================================================== */
function initThemeToggle() {
  const btnDesktop = document.getElementById('themeToggleBtn');
  const btnMobile = document.getElementById('themeToggleBtnMobile');

  function toggleTheme() {
    const currentTheme = document.documentElement.getAttribute('data-theme') || 'dark';
    const nextTheme = currentTheme === 'dark' ? 'light' : 'dark';
    document.documentElement.setAttribute('data-theme', nextTheme);
    localStorage.setItem('verve_theme', nextTheme);
  }

  if (btnDesktop) btnDesktop.addEventListener('click', toggleTheme);
  if (btnMobile) btnMobile.addEventListener('click', toggleTheme);
}

/* ==========================================================================
   2. Carousel Controller
   ========================================================================== */
function initCarousels() {
  const carousels = document.querySelectorAll('.carousel-container');

  carousels.forEach(carousel => {
    const track = carousel.querySelector('.carousel-track');
    const slides = carousel.querySelectorAll('.carousel-slide');
    const prevBtn = carousel.querySelector('.carousel-nav.prev');
    const nextBtn = carousel.querySelector('.carousel-nav.next');
    const indicators = carousel.querySelectorAll('.indicator-dot');

    if (!slides.length) return;

    let currentIndex = 0;

    function updateCarousel() {
      track.style.transform = `translateX(-${currentIndex * 100}%)`;
      indicators.forEach((dot, idx) => {
        dot.classList.toggle('active', idx === currentIndex);
      });

      if (prevBtn) prevBtn.style.display = currentIndex === 0 ? 'none' : 'flex';
      if (nextBtn) nextBtn.style.display = currentIndex === slides.length - 1 ? 'none' : 'flex';
    }

    if (prevBtn) {
      prevBtn.addEventListener('click', (e) => {
        e.stopPropagation();
        if (currentIndex > 0) {
          currentIndex--;
          updateCarousel();
        }
      });
    }

    if (nextBtn) {
      nextBtn.addEventListener('click', (e) => {
        e.stopPropagation();
        if (currentIndex < slides.length - 1) {
          currentIndex++;
          updateCarousel();
        }
      });
    }

    // Touch Swipe support
    let startX = 0;
    let isSwiping = false;

    carousel.addEventListener('touchstart', (e) => {
      startX = e.touches[0].clientX;
      isSwiping = true;
    }, { passive: true });

    carousel.addEventListener('touchend', (e) => {
      if (!isSwiping) return;
      const endX = e.changedTouches[0].clientX;
      const diffX = startX - endX;

      if (Math.abs(diffX) > 40) {
        if (diffX > 0 && currentIndex < slides.length - 1) {
          currentIndex++;
        } else if (diffX < 0 && currentIndex > 0) {
          currentIndex--;
        }
        updateCarousel();
      }
      isSwiping = false;
    }, { passive: true });

    updateCarousel();
  });
}

/* ==========================================================================
   3. Double-Tap / Double-Click Heart Burst Animation
   ========================================================================== */
function initDoubleTapLike() {
  const targets = document.querySelectorAll('.double-tap-target');

  targets.forEach(target => {
    let lastTap = 0;

    target.addEventListener('click', (e) => {
      const currentTime = new Date().getTime();
      const tapLength = currentTime - lastTap;

      // Handle double click or fast double tap
      if (tapLength < 300 && tapLength > 0) {
        e.preventDefault();
        const overlay = target.querySelector('.heart-burst-overlay');
        const postWrap = target.closest('.story-card');
        const postId = postWrap ? postWrap.dataset.postId : null;

        if (overlay) {
          overlay.classList.remove('animate');
          void overlay.offsetWidth; // Trigger reflow
          overlay.classList.add('animate');
        }

        if (postId) {
          performLike(postId, true);
        }
      }
      lastTap = currentTime;
    });
  });
}

/* ==========================================================================
   4. Social Action Buttons (Like, Save, Share)
   ========================================================================== */
function initSocialActionButtons() {
  // Like buttons
  document.querySelectorAll('.btn-like').forEach(btn => {
    btn.addEventListener('click', () => {
      const postId = btn.dataset.postId;
      performLike(postId);
    });
  });

  // Save / Bookmark buttons
  document.querySelectorAll('.btn-save').forEach(btn => {
    btn.addEventListener('click', async () => {
      const postId = btn.dataset.postId;
      try {
        const res = await fetch(`/api/posts/${postId}/save`, { method: 'POST' });
        const data = await res.json();
        btn.classList.toggle('saved', data.saved);

        if (data.saved) {
          showToast('✨ Saved to Favorites collection');
        } else {
          showToast('Removed from saved photos');
        }
      } catch (err) {
        showToast('Error saving photo');
      }
    });
  });

  // Share buttons
  document.querySelectorAll('.btn-share').forEach(btn => {
    btn.addEventListener('click', () => {
      const url = window.location.href;
      if (navigator.clipboard) {
        navigator.clipboard.writeText(url);
        showToast('🔗 Story link copied to clipboard!');
      } else {
        showToast('Shared successfully!');
      }
    });
  });

  // Comment focus buttons
  document.querySelectorAll('.btn-comment-focus').forEach(btn => {
    btn.addEventListener('click', () => {
      const postId = btn.dataset.postId;
      const postCard = document.querySelector(`.story-card[data-post-id="${postId}"]`);
      if (postCard) {
        const input = postCard.querySelector('.comment-input');
        if (input) input.focus();
      }
    });
  });
}

async function performLike(postId, forceLike = false) {
  const btn = document.querySelector(`.btn-like[data-post-id="${postId}"]`);
  const countEl = document.getElementById(`likes-count-${postId}`);

  if (forceLike && btn && btn.classList.contains('liked')) {
    return; // Already liked on double tap
  }

  try {
    const res = await fetch(`/api/posts/${postId}/like`, { method: 'POST' });
    const data = await res.json();

    if (btn) btn.classList.toggle('liked', data.liked);
    if (countEl) countEl.textContent = data.likes_count;
  } catch (err) {
    console.error('Like error', err);
  }
}

/* ==========================================================================
   5. Inline Comments Submission
   ========================================================================== */
function initInlineComments() {
  document.querySelectorAll('.inline-comment-form').forEach(form => {
    form.addEventListener('submit', async (e) => {
      e.preventDefault();
      const postId = form.dataset.postId;
      const input = form.querySelector('.comment-input');
      const text = input.value.trim();

      if (!text) return;

      const formData = new FormData();
      formData.append('text', text);

      try {
        const res = await fetch(`/api/posts/${postId}/comment`, {
          method: 'POST',
          body: formData
        });

        if (res.ok) {
          const data = await res.json();
          const list = document.getElementById(`comments-list-${postId}`);
          if (list) {
            const item = document.createElement('div');
            item.className = 'comment-item';

            const avatarWrap = document.createElement('div');
            avatarWrap.className = 'comment-avatar';

            const avatar = document.createElement('img');
            avatar.src = data.comment.user_avatar;
            avatar.alt = data.comment.username;
            avatarWrap.appendChild(avatar);

            const content = document.createElement('div');
            content.className = 'comment-content';

            const user = document.createElement('span');
            user.className = 'comment-user';
            user.textContent = data.comment.username;

            const textEl = document.createElement('span');
            textEl.className = 'comment-text';
            textEl.textContent = data.comment.text;

            const time = document.createElement('span');
            time.className = 'comment-time';
            time.textContent = data.comment.time_ago;

            content.append(user, textEl, time);
            item.append(avatarWrap, content);
            list.appendChild(item);
          }
          input.value = '';
          showToast('Comment posted');
        }
      } catch (err) {
        showToast('Could not post comment');
      }
    });
  });

  // Toggle hidden comments
  document.querySelectorAll('.btn-toggle-comments').forEach(btn => {
    btn.addEventListener('click', () => {
      const postId = btn.dataset.postId;
      const list = document.getElementById(`comments-list-${postId}`);
      if (list) {
        list.querySelectorAll('.comment-item.comment-hidden').forEach(el => {
          el.classList.remove('comment-hidden');
        });
        btn.style.display = 'none';
      }
    });
  });
}

/* ==========================================================================
   6. Create Post Multi-Step Wizard Modal Controller
   ========================================================================== */
function initCreatePostWizard() {
  const modal = document.getElementById('createModalOverlay');
  const btnTriggers = document.querySelectorAll('.btn-create-trigger');
  const btnClose = document.getElementById('btnCreateClose');
  const btnBack = document.getElementById('btnCreateBack');
  const btnPrev = document.getElementById('btnCreatePrev');
  const btnNext = document.getElementById('btnCreateNext');
  const btnPublish = document.getElementById('btnCreatePublish');

  const fileInput = document.getElementById('fileInput');
  const btnChooseFiles = document.getElementById('btnChooseFiles');
  const dropzone = document.getElementById('uploadDropzone');
  const selectedFilesList = document.getElementById('selectedFilesList');

  if (!modal) return;

  let currentStep = 1;
  let selectedFiles = [];
  let selectedLayout = 'clean';
  let selectedAspectRatio = '4:5';

  function openModal() {
    currentStep = 1;
    selectedFiles = [];
    updateStepUI();
    modal.classList.add('active');
  }

  function closeModal() {
    modal.classList.remove('active');
  }

  btnTriggers.forEach(b => b.addEventListener('click', openModal));
  if (btnClose) btnClose.addEventListener('click', closeModal);

  // File Selection
  if (btnChooseFiles) btnChooseFiles.addEventListener('click', () => fileInput.click());

  if (fileInput) {
    fileInput.addEventListener('change', (e) => {
      handleFiles(Array.from(e.target.files));
    });
  }

  // Drag & drop
  if (dropzone) {
    ['dragenter', 'dragover'].forEach(name => {
      dropzone.addEventListener(name, (e) => {
        e.preventDefault();
        dropzone.classList.add('dragover');
      });
    });

    ['dragleave', 'drop'].forEach(name => {
      dropzone.addEventListener(name, (e) => {
        e.preventDefault();
        dropzone.classList.remove('dragover');
      });
    });

    dropzone.addEventListener('drop', (e) => {
      if (e.dataTransfer.files.length) {
        handleFiles(Array.from(e.dataTransfer.files));
      }
    });
  }

  function handleFiles(files) {
    selectedFiles = files.filter(f => f.type.startsWith('image/'));
    renderSelectedFiles();
    if (selectedFiles.length > 0) {
      fetchSmartLayoutProposal();
    }
  }

  function renderSelectedFiles() {
    if (!selectedFilesList) return;
    selectedFilesList.innerHTML = '';
    selectedFiles.forEach(file => {
      const img = document.createElement('img');
      img.src = URL.createObjectURL(file);
      img.className = 'selected-thumb';
      selectedFilesList.appendChild(img);
    });
    renderArrangementPreview();
  }

  // Fetch Smart Auto Arrange Proposal
  async function fetchSmartLayoutProposal() {
    const formData = new FormData();
    formData.append('files_count', selectedFiles.length);

    try {
      const res = await fetch('/api/smart-arrange', {
        method: 'POST',
        body: formData
      });
      const data = await res.json();

      selectedLayout = data.recommended_layout;

      const heading = document.getElementById('smartArrangeHeading');
      const subtext = document.getElementById('smartArrangeSubtext');
      if (heading) heading.textContent = data.message_en;
      if (subtext) subtext.textContent = data.message_th;

      renderLayoutOptions(data.available_styles, data.recommended_layout);
    } catch (err) {
      console.error('Smart arrange error', err);
    }
  }

  function renderLayoutOptions(styles, recommended) {
    const grid = document.getElementById('layoutOptionsGrid');
    if (!grid) return;
    grid.innerHTML = '';

    styles.forEach(st => {
      const isSelected = st.id === selectedLayout;
      const card = document.createElement('div');
      card.className = `layout-option-card ${isSelected ? 'selected' : ''}`;
      card.dataset.layout = st.id;
      card.innerHTML = `
        <div class="layout-option-name">${st.name}</div>
        <div class="layout-option-desc">${st.desc}</div>
      `;
      card.addEventListener('click', () => {
        grid.querySelectorAll('.layout-option-card').forEach(c => c.classList.remove('selected'));
        card.classList.add('selected');
        selectedLayout = st.id;
        renderArrangementPreview();
      });
      grid.appendChild(card);
    });
  }

  // Step Navigation
  if (btnNext) {
    btnNext.addEventListener('click', () => {
      if (currentStep === 1 && selectedFiles.length === 0) {
        showToast('Please select at least one image');
        return;
      }
      if (currentStep < 4) {
        currentStep++;
        updateStepUI();
      }
    });
  }

  if (btnPrev) {
    btnPrev.addEventListener('click', () => {
      if (currentStep > 1) {
        currentStep--;
        updateStepUI();
      }
    });
  }

  document.querySelectorAll('.preset-pill').forEach(pill => {
    pill.addEventListener('click', () => {
      document.querySelectorAll('.preset-pill').forEach(el => el.classList.remove('active'));
      pill.classList.add('active');
      selectedAspectRatio = pill.dataset.ratio || '4:5';
      renderArrangementPreview();
    });
  });

  const smartLayoutBtn = document.getElementById('btnApplySmartLayout');
  if (smartLayoutBtn) {
    smartLayoutBtn.addEventListener('click', () => {
      document.querySelectorAll('.layout-option-card').forEach(card => {
        card.classList.toggle('selected', card.dataset.layout === selectedLayout);
      });
      showToast('Layout applied');
    });
  }

  function renderArrangementPreview() {
    const preview = document.getElementById('imageArrangementPreview');
    if (!preview) return;

    preview.innerHTML = '';
    preview.dataset.ratio = selectedAspectRatio;
    preview.dataset.layout = selectedLayout;

    selectedFiles.forEach(file => {
      const frame = document.createElement('div');
      frame.className = 'preview-frame';

      const img = document.createElement('img');
      img.src = URL.createObjectURL(file);
      img.alt = file.name;

      frame.appendChild(img);
      preview.appendChild(frame);
    });
  }

  function updateStepUI() {
    document.querySelectorAll('.create-step').forEach((el, idx) => {
      el.classList.toggle('active', idx + 1 === currentStep);
    });

    if (btnPrev) btnPrev.style.display = currentStep > 1 ? 'inline-flex' : 'none';
    if (btnNext) btnNext.style.display = currentStep < 4 ? 'inline-flex' : 'none';
    if (btnPublish) btnPublish.style.display = currentStep === 4 ? 'inline-flex' : 'none';

    const titleEl = document.getElementById('createModalTitle');
    if (titleEl) {
      const titles = ['Choose Images', 'Smart Auto Arrange', 'Make It Yours', 'Preview & Publish'];
      titleEl.textContent = titles[currentStep - 1] || 'Create Post';
    }
  }

  // Publish Post Handler
  if (btnPublish) {
    btnPublish.addEventListener('click', async () => {
      if (!selectedFiles.length) return;

      btnPublish.disabled = true;
      btnPublish.textContent = 'Publishing...';

      const formData = new FormData();
      selectedFiles.forEach(file => formData.append('files', file));

      const titleInput = document.getElementById('postTitleInput');
      const captionInput = document.getElementById('postCaptionInput');
      const tagsInput = document.getElementById('postTagsInput');
      const locationInput = document.getElementById('postLocationInput');

      if (titleInput) formData.append('title', titleInput.value);
      if (captionInput) formData.append('caption', captionInput.value);
      if (tagsInput) formData.append('tags', tagsInput.value);
      if (locationInput) formData.append('location', locationInput.value);

      formData.append('layout_style', selectedLayout);
      formData.append('aspect_ratio', selectedAspectRatio);

      try {
        const res = await fetch('/api/posts/create', {
          method: 'POST',
          body: formData
        });

        if (res.ok) {
          showToast('✨ Your post is ready. Story published!');
          closeModal();
          setTimeout(() => window.location.reload(), 800);
        } else {
          showToast('Error uploading post');
          btnPublish.disabled = false;
          btnPublish.textContent = 'Publish Story';
        }
      } catch (err) {
        showToast('Network error');
        btnPublish.disabled = false;
        btnPublish.textContent = 'Publish Story';
      }
    });
  }
}

/* ==========================================================================
   7. Profile Page Tabs
   ========================================================================== */
function initProfileTabs() {
  const tabs = document.querySelectorAll('.profile-tabs .tab-btn');
  tabs.forEach(tab => {
    tab.addEventListener('click', () => {
      tabs.forEach(t => t.classList.remove('active'));
      tab.classList.add('active');

      const targetTab = tab.dataset.tab;
      document.querySelectorAll('.profile-tab-content').forEach(content => {
        if (content.id === `tab-${targetTab}`) {
          content.style.display = 'block';
          content.classList.add('active');
        } else {
          content.style.display = 'none';
          content.classList.remove('active');
        }
      });
    });
  });
}

/* ==========================================================================
   8. Quick Lightbox
   ========================================================================== */
function initQuickLightbox() {
  const overlay = document.getElementById('lightboxOverlay');
  const closeBtn = document.getElementById('lightboxCloseBtn');
  const lightboxImg = document.getElementById('lightboxImg');

  if (!overlay) return;

  document.querySelectorAll('.masonry-item, .photo-grid-item').forEach(item => {
    item.addEventListener('click', () => {
      const img = item.querySelector('img');
      if (img && lightboxImg) {
        lightboxImg.src = img.src;
        overlay.classList.add('active');
      }
    });
  });

  if (closeBtn) {
    closeBtn.addEventListener('click', () => overlay.classList.remove('active'));
  }
  overlay.addEventListener('click', (e) => {
    if (e.target === overlay) overlay.classList.remove('active');
  });
}

/* ==========================================================================
   9. Helper: Toast Notification
   ========================================================================== */
function showToast(message) {
  const container = document.getElementById('toastContainer');
  if (!container) return;

  const toast = document.createElement('div');
  toast.className = 'toast-message';
  toast.textContent = message;

  container.appendChild(toast);

  setTimeout(() => {
    toast.style.opacity = '0';
    toast.style.transform = 'translateY(10px)';
    toast.style.transition = 'all 0.3s ease';
    setTimeout(() => toast.remove(), 300);
  }, 2500);
}
