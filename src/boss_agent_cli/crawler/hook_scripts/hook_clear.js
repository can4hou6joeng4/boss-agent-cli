// ==UserScript==
// @name         hook_clear
// @namespace    https://github.com/0xsdeo/Hook_JS
// @version      2024-11-09
// @description  禁止js清除控制台数据
// @author       0xsdeo
// @match        http://*/*
// @icon         data:image/gif;base64,R0lGODlhAQABAAAAACH5BAEKAAEALAAAAAABAAEAAAICTAEAOw==
// @grant        none
// ==/UserScript==

(function() {
    'use strict';

    console.clear = function() {};
})();
