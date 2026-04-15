gsap.registerPlugin(ScrollTrigger);

document.addEventListener('DOMContentLoaded', function () {

  initHeroEntrance('.hero-inner', '.js-hero-el');

  initParallax([
    { selector: '.hero-backdrop', speed: 0.5 },
    { selector: '.hero::before', speed: 0.7 }
  ]);

  initNavScroll();
  initMobileNav();
  initFeatureReveals();
  initFaqAccordion();
  initStatCounters();
  initSectionReveals();

  ScrollTrigger.refresh();
});

function initHeroEntrance(containerSelector, itemSelector) {
  gsap.from(itemSelector || containerSelector + ' > *', {
    y: 32,
    opacity: 0,
    filter: 'blur(6px)',
    duration: 0.9,
    ease: 'power3.out',
    stagger: 0.12,
    clearProps: 'filter'
  });
}

function initParallax(layers) {
  layers.forEach(function (layer) {
    var el = document.querySelector(layer.selector);
    if (!el) return;
    gsap.to(layer.selector, {
      y: function () { return window.innerHeight * (layer.speed - 1) * -0.4; },
      ease: 'none',
      scrollTrigger: {
        trigger: 'body',
        start: 'top top',
        end: 'bottom bottom',
        scrub: layer.speed
      }
    });
  });
}

function initNavScroll() {
  var nav = document.querySelector('.split-nav');
  if (!nav) return;
  var scrolled = false;
  ScrollTrigger.create({
    start: 'top top',
    end: '+=80',
    onUpdate: function (self) {
      if (self.progress > 0.5 && !scrolled) {
        nav.classList.add('is-scrolled');
        scrolled = true;
      } else if (self.progress <= 0.5 && scrolled) {
        nav.classList.remove('is-scrolled');
        scrolled = false;
      }
    }
  });
}

function initMobileNav() {
  var toggle = document.querySelector('.nav-toggle');
  var overlay = document.querySelector('.mobile-nav-overlay');
  if (!toggle || !overlay) return;
  toggle.addEventListener('click', function () {
    var isOpen = toggle.classList.toggle('is-open');
    overlay.classList.toggle('is-open');
    toggle.setAttribute('aria-expanded', isOpen);
    document.body.style.overflow = isOpen ? 'hidden' : '';
  });
  overlay.querySelectorAll('.mobile-nav-item').forEach(function (item) {
    item.addEventListener('click', function () {
      toggle.classList.remove('is-open');
      overlay.classList.remove('is-open');
      toggle.setAttribute('aria-expanded', 'false');
      document.body.style.overflow = '';
    });
  });
}

function initFeatureReveals() {
  document.querySelectorAll('.js-feature').forEach(function (block) {
    ScrollTrigger.create({
      trigger: block,
      start: 'top 85%',
      once: true,
      onEnter: function () { block.classList.add('is-visible'); }
    });
  });
}

function initFaqAccordion() {
  document.querySelectorAll('.faq-item').forEach(function (item) {
    var trigger = item.querySelector('.faq-trigger');
    trigger.addEventListener('click', function () {
      var isOpen = item.getAttribute('data-open') === 'true';
      document.querySelectorAll('.faq-item').forEach(function (other) {
        other.setAttribute('data-open', 'false');
        other.querySelector('.faq-trigger').setAttribute('aria-expanded', 'false');
      });
      if (!isOpen) {
        item.setAttribute('data-open', 'true');
        trigger.setAttribute('aria-expanded', 'true');
      }
    });
  });
}

function initStatCounters() {
  document.querySelectorAll('.stat-num').forEach(function (el) {
    var target = parseInt(el.getAttribute('data-target'), 10);
    if (isNaN(target) || target === 0) return;
    var obj = { val: 0 };
    gsap.to(obj, {
      val: target,
      duration: 1.8,
      ease: 'power2.out',
      scrollTrigger: {
        trigger: el,
        start: 'top 85%',
        once: true
      },
      onUpdate: function () { el.textContent = Math.round(obj.val); }
    });
  });
}

function initSectionReveals() {
  document.querySelectorAll('.faq-title, .cta-headline').forEach(function (el) {
    gsap.from(el, {
      opacity: 0,
      y: 30,
      duration: 0.8,
      ease: 'power3.out',
      scrollTrigger: {
        trigger: el,
        start: 'top 85%',
        once: true
      }
    });
  });

  document.querySelectorAll('.faq-item').forEach(function (item, i) {
    gsap.from(item, {
      opacity: 0,
      x: 20,
      duration: 0.5,
      delay: i * 0.06,
      scrollTrigger: {
        trigger: item,
        start: 'top 90%',
        once: true
      }
    });
  });

  document.querySelectorAll('.pipeline-step').forEach(function (step, i) {
    gsap.from(step, {
      opacity: 0,
      scale: 0.9,
      duration: 0.4,
      delay: i * 0.08,
      ease: 'back.out(1.5)',
      scrollTrigger: {
        trigger: step,
        start: 'top 90%',
        once: true
      }
    });
  });

  gsap.from('.install-code', {
    opacity: 0,
    y: 20,
    duration: 0.6,
    scrollTrigger: {
      trigger: '.install-code',
      start: 'top 85%',
      once: true
    }
  });
}
