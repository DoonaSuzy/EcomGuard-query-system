            // $.ajax({
            // 	url: 'https://public.creditchina.gov.cn/admin/auth-user/public/getUserInfo',
            // 	type: 'GET',
            // 	xhrFields: {
            // 		withCredentials: true
            // 	},
            // 	cache: false,
            // 	//async: false,
            // 	success: function (response) {
            // 		$('.login').html('<a href="https://public.creditchina.gov.cn/htmls/personal/center.html">' + response.data.fullname + '</a>')
            // 		//$('.login').text(''+response.data.fullname);
            // 		//$('.login a').attr('href','https://public.creditchina.gov.cn/htmls/personal/center.html');
            // 		$('.reg').hide();

            // 		//console.log(response)
            // 	},
            // 	error: function (response) {
            // 		//                    console.log('未登录');

            // 	}
            // });

            (function () {
            	// 公用方法
            	window.Util = {
            		plat: (function () {
            			var res = {},
            				ua = navigator.userAgent,
            				mobileReg = /android|webos|ip(hone|ad|od)|opera (mini|mobi|tablet)|iemobile|windows.+(phone|touch)|mobile|fennec|kindle (Fire)|Silk|maemo|blackberry|playbook|bb10\; (touch|kbd)|Symbian(OS)|Ubuntu Touch/i,
            				iosReg = /ip(hone|ad|od)/i;
            			if (!ua) {
            				ua = "";
            			}
            			ua = ua.toLowerCase();
            			res.isMobile = mobileReg.test(ua) || res.isNull;
            			res.isPc = !res.isMobile;
            			/**
            			 * 通过传入特殊ua串，判断是否是该平台
            			 * @param uastr
            			 * @returns {boolean}
            			 */
            			res.is = function (uastr) {
            				if (!uastr) {
            					return false;
            				}
            				uastr = uastr.toLowerCase();

            				if (ua.indexOf(uastr) > -1) {
            					return true;
            				} else {
            					return false;
            				}
            			}
            			return res;
            		}()),
            		trim: (function () {
            			var trimReg = /(^\s*)|(\s*$)/g;
            			return function (s) {
            				if (!s || (typeof s !== "string")) {
            					return s;
            				}
            				return s.replace(trimReg, '');
            			}
            		})(),
            		isBlank: function (obj) {
            			return (obj === undefined || obj === null || util.trim(obj) === "");
            		},
            		isNull: function (o) {
            			return (!o && (o !== false) && (o !== 0));
            		},
            		objectType: (function (param) {
            			var objectTypeReg = /^\[object (.*)\]$/;
            			return function (obj) {
            				if (typeof obj == "object" || typeof obj == "function") {
            					return Object.prototype.toString.call(obj).match(objectTypeReg)[1];
            				} else {
            					return undefined;
            				}
            			}
            		})(),
            		endWith: function (str, endStr) {
            			if (!str || !endStr || str.length == 0 || (endStr.length > str.length))
            				return false;
            			if (str.substr(str.length - endStr.length) == endStr)
            				return true;
            			else
            				return false;
            		},
            		statWith: function (str, startStr) {
            			if (!str || !startStr || str.length == 0 || (startStr.length > str.length))
            				return false;
            			if (str.substr(0, startStr.length) == startStr)
            				return true;
            			else
            				return false;
            		},
            		hasOwn: function (obj, fn) {
            			var k, v;
            			for (k in obj) {
            				if (obj.hasOwnProperty(k)) {
            					v = obj[k];
            					fn && (fn.call(window, k, v));
            				}
            			}
            		},
            		stringifyObject: function (obj) {
            			if (Util.objectType(obj) !== "Object") {
            				if (Util.isBlank(obj) || (typeof obj !== "string")) {
            					return "";
            				}
            				return obj;
            			}
            			var r = "";
            			Util.hasOwn(obj, function (key, value) {
            				r += "&" + key + "=" + encodeURIComponent(value);
            			});
            			return r.substr(1);
            		},
            		appendParam: function (url, param) {
            			if (Util.isNull(param)) {
            				return url;
            			}
            			if (Util.objectType(param) == "Object") {
            				param = Util.stringifyObject(param);
            			}
            			if (!url) {
            				url = "";
            			}
            			if (Util.endWith(url, "?")) {
            				url += param;
            			} else if (Util.endWith(url, "&")) {
            				if (url.indexOf("?") >= 0) {
            					url += param;
            				} else {
            					url += "?" + param;
            				}
            			} else {
            				if (url.indexOf("?") >= 0) {
            					url += "&" + param;
            				} else {
            					url += "?" + param;
            				}
            			}
            			return url;
            		},
            		parseStringifiedObject: function (str) {
            			if (typeof str !== "string") {
            				if (typeof str == "object") {
            					return str;
            				}
            				return {};
            			}

            			var r = {},
            				strArr = str.split("&"),
            				len = strArr.length,
            				i, t, p, k, v;
            			for (i = 0; i < len; i++) {
            				t = strArr[i];
            				p = t.indexOf("=");
            				if (p <= 0) {
            					continue;
            				}
            				k = t.substring(0, p);
            				v = decodeURIComponent(t.substr(p + 1));
            				r[k] = v;
            			}

            			return r;
            		},
            		query: function () {
            			var queryStr = location.search;
            			if (queryStr.length > 0) {
            				queryStr = queryStr.substr(1);
            			}
            			// 兼容客户端在hash后加参数问题
            			var hashes = location.hash.match(/^#([^?^&]*)[\?&]/);
            			if (hashes && hashes.length > 0) {
            				queryStr = queryStr + (queryStr.length ? "&" : "") + location.hash.replace(hashes[0], "");
            				history.replaceState({}, "", location.origin + location.pathname + "?" + queryStr + "#" + hashes[1]);
            			}
            			return Util.parseStringifiedObject(queryStr);
            		},
            	}
            })()
