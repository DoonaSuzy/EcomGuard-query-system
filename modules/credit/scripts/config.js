/**
 * Created by issuser on 2017/8/23. 1
 */
var _hmt = _hmt || [];
(function() {
  var hm = document.createElement("script");
  hm.src = "https://hm.baidu.com/hm.js?0076fef7e919d8d7b24383dc8f1c852a";
  var s = document.getElementsByTagName("script")[0];
  s.parentNode.insertBefore(hm, s);
})();
window.config={
    IP:'https://public.creditchina.gov.cn/private-api/',
    FEIP :"https://www.creditchina.gov.cn/",
    VcodeIP :"https://public.creditchina.gov.cn/private-api/"
};
if (!Array.prototype.indexOf)
  Array.prototype.indexOf = function(searchValue, index) {
    // In non-strict-mode, if the `this` variable is null
    // or undefined, then it is set the the window object.
    // Else, `this` is automaticly converted to an object.
    var len = this.length >>> 0; // convert ot number or 0

    index |= 0;              // rounds and NaN-checks
    if (index < 0)             // check if negative start
      index = Math.max(len - index, 0);
    else if (index >= len) // else, check if too big
      return -1;

    if (searchValue === undefined)
      // Because searchValue is undefined, keys that
      // don't exist will have the same value as the
      // searchValue, and thus do need to be checked.
      do {
        if (index in this && this[index] === undefined)
          return index;
      } while (++index !== len)
    else
      // Because searchValue is not undefined, there's no
      // need to check if the current key is in the array
      // because if the key isn't in the array, then it's
      // undefined which is not equal to the searchValue.
      do {
        if (this[index] === searchValue)
          return index;
      } while (++index !== len)

    // if nothing was found, then simply return -1
    return -1;
  };

// pv 统计
(function () {
    jQuery.ajax({
        url: config.IP + "info?" + GetUrlRelativePath(),
        type: 'GET',
        success: function (res) {
        },
        error: function (error) {
        },
    });
})()

function GetUrlRelativePath() {
    var url = document.location.toString();
    var arrUrl = url.split("//");

    var start = arrUrl[1].indexOf("/");
    var relUrl = arrUrl[1].substring(start);//stop省略，截取从start开始到结尾的所有字符

    if (relUrl.indexOf("?") != -1) {
        relUrl = relUrl.split("?")[0];
    }
    if(relUrl == "/xinyongxinxixiangqing/xiazai/index.html") {
       relUrl = "/home/xinyongxinxixiangqing/xiazailiulan/"
    }
    return relUrl;
}


document.write('<scr'+'ipt src="https://www.creditchina.gov.cn/js/vcode/vcode.js"></scri'+'pt>');

//document.write('<scr'+'ipt src="https://www.creditchina.gov.cn/js2025/object-defineproperty-ie8.js"></scri'+'pt>')
//document.write('<scr'+'ipt src="https://www.creditchina.gov.cn/js2025/olym-sm9-combine-gjxxzx.min.js"></scri'+'pt>')
//document.write('<scr'+'ipt src="https://www.creditchina.gov.cn/js/sm9/encrypt.js"></scri'+'pt>')
//document.write('<scr'+'ipt src="https://www.creditchina.gov.cn/js/sm9/jsSdkmac.js"></scri'+'pt>')