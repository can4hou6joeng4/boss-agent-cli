// ==UserScript==
// @name         hook_history
// @namespace    https://github.com/0xsdeo/Hook_JS
// @version      2024-11-09
// @description  重写go和back方法，以此来避免网站反调试返回上一页或某个特定历史页面
// @author       0xsdeo
// @match        http://*/*
// @icon         data:image/gif;base64,R0lGODlhAQABAAAAACH5BAEKAAEALAAAAAABAAEAAAICTAEAOw==
// @grant        none
// ==/UserScript==

(function() {
    'use strict';

    window.history.go = function() {};
    window.history.back = function () {};
})();
