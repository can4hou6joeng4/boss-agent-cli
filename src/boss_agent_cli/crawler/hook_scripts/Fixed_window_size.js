// ==UserScript==
// @name         Fixed_window_size
// @namespace    https://github.com/0xsdeo/Hook_JS
// @version      2025-01-11
// @description  js获取浏览器高度或宽度时获取到的是脚本中设置的固定值，如果网站js尝试去设置高度和宽度值会直接去设置固定值，并不会按照网站js设置的值去设置。
// @author       0xsdeo
// @match        http://*/*
// @icon         data:image/gif;base64,R0lGODlhAQABAAAAACH5BAEKAAEALAAAAAABAAEAAAICTAEAOw==
// @grant        none
// ==/UserScript==

(function () {
    'use strict';

    let inner_height = 660; // 固定的高度值
    let inner_width = 1366; // 固定的宽度值

    let outer_height = 760; // 固定的高度值
    let outer_width = 1400; // 固定的宽度值

    let innerHeight_property_accessor = Object.getOwnPropertyDescriptor(window, "innerHeight"); // 获取innerHeight属性访问器描述符
    let innerHeight_set_accessor = innerHeight_property_accessor.set; // 获取setter

    Object.defineProperty(window, "innerHeight", {
        get: function () {
            // 在这里写你想让hook后的属性在被获取值时执行的代码
            return inner_height;
        },
        set: function () {
            // 在这里写你想让hook后的属性在被设置值时执行的代码
            innerHeight_set_accessor.call(window, inner_height);// 将网站js设置目标属性值时所传入的内容传给原setter设置并返回结果
        }
    });

    let innerWidth_property_accessor = Object.getOwnPropertyDescriptor(window, "innerWidth"); // 获取innerWidth属性访问器描述符
    let innerWidth_set_accessor = innerWidth_property_accessor.set; // 获取setter

    Object.defineProperty(window, "innerWidth", {
        get: function () {
            // 在这里写你想让hook后的属性在被获取值时执行的代码
            return inner_width;
        },
        set: function () {
            // 在这里写你想让hook后的属性在被设置值时执行的代码
            innerWidth_set_accessor.call(window, inner_width);// 将网站js设置目标属性值时所传入的内容传给原setter设置并返回结果
        }
    });

    let outerWidth_property_accessor = Object.getOwnPropertyDescriptor(window, "outerWidth"); // 获取outerWidth属性访问器描述符
    let outerWidth_set_accessor = outerWidth_property_accessor.set; // 获取setter

    Object.defineProperty(window, "outerWidth", {
        get: function () {
            // 在这里写你想让hook后的属性在被获取值时执行的代码
            return outer_width;
        },
        set: function () {
            // 在这里写你想让hook后的属性在被设置值时执行的代码
            outerWidth_set_accessor.call(window, outer_width);// 将网站js设置目标属性值时所传入的内容传给原setter设置并返回结果
        }
    });

    let outerHeight_property_accessor = Object.getOwnPropertyDescriptor(window, "outerHeight"); // 获取innerWidth属性访问器描述符
    let outerHeight_set_accessor = outerHeight_property_accessor.set; // 获取setter

    Object.defineProperty(window, "outerHeight", {
        get: function () {
            // 在这里写你想让hook后的属性在被获取值时执行的代码
            return outer_height;
        },
        set: function () {
            // 在这里写你想让hook后的属性在被设置值时执行的代码
            outerHeight_set_accessor.call(window, outer_height);// 将网站js设置目标属性值时所传入的内容传给原setter设置并返回结果
        }
    });
})();