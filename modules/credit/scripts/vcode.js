var vcodeswitch = 1;   //是否开启验证码功能： 1开启 0关闭

document.write('<style>');
document.write('.mask {position: fixed;left: 0;top: 0;width: 100%;height: 100%;background: rgba(0, 0, 0, 0.2);z-index: 99998;}');
document.write('.vcodepop {box-sizing: border-box;position: fixed;left: 50%;top: 50%;transform: translate(-50%, -50%);width: 400px;height: 200px;background: #fff;border-radius: 4px;padding: 25px 20px;z-index: 99999;}');
document.write('.vcodepop .vcodeinputbox {float: left;width: 210px;height: 76px;}');
document.write('.vcodepop input {box-sizing: border-box;width: 210px;height: 46px;line-height: 46px;border: 1px solid #CCD3D9; border-radius:4px;background: #fff;font-size: 14px;padding: 0 15px;}');
document.write('.vcodepop input:focus{border: 1px solid #B1B7BC!important;}');
document.write('.vcodepop .vcodeinputbox p {height: 20px;line-height: 20px;font-size:12px;background: url("https://www.creditchina.gov.cn/new2020/images/errortip.png") left center no-repeat;padding-left: 16px;margin-top: 10px;color: #BE1B2F;display: none;width:210px;box-sizing: border-box;}');
document.write('.vcodepop .vcodeimgbox {float: right;width: 130px;height: 76px;}');
document.write('.vcodepop .vcodeimgbox img {width: 130px;height: 46px;display: block;vertical-align: top;cursor: pointer;}');
document.write('.vcodepop .vcodeimgbox span {display: block;height: 20px;line-height: 20px;font-size:14px;color: #9E9E9E;text-align: center;margin-top: 10px;cursor: pointer;}');
document.write('.vcodepop .buttongroup {display: flex;justify-content: space-between;width: 240px;margin: 35px auto 0;overflow: hidden;}');
document.write('.vcodepop .buttongroup a {width: 110px;height: 40px;line-height: 40px;font-size: 16px;text-align: center;border-radius: 4px;-webkit-border-radius: 4px;-moz-border-radius: 4px;-ms-border-radius: 4px;-o-border-radius: 4px;cursor: pointer;}');
document.write('.vcodepop .buttongroup a.confirm {background: #BE1B2F;color: #fff;}');
document.write('.vcodepop .buttongroup a.cancel {border: 1px solid #E8E8E8;color: #333;}');
document.write('.clr {margin: 0px;padding: 0px;height: 0px;width: 0px;overflow: hidden;clear: both;}')
document.write('</style>');
document.write('<div class="mask" style="display:none"></div>');
document.write('<div class="vcodepop" style="display:none">');
document.write('<div class="vcodeinputbox"><input type="text" placeholder="请输入验证码" id="vcode" autocomplete="off" /><p class="errortip">验证码错误</p></div>');
document.write('<div class="vcodeimgbox"><img id="vcodeimg" src="" /><span>看不清? 换一张</span></div>');
document.write('<p class="clr"></p>');
document.write('<div class="buttongroup"><a class="cancel">取消</a><a class="confirm" >验证</a></div>');
document.write('</div>');


  if(getCookie("cookienum")){

  }else{
    var cookienum =  Math.random();
    setCookie("cookienum", cookienum,1);
  }

  window.addEventListener('beforeunload',function(){
    clearCookie(cookienum)
  })
  
//   console.log(getCookie('cookienum'))
$('.vcodepop .cancel').click(function () {
    // updateVocdeFun();
    $('.vcodepop .vcodeinputbox p').html('').hide()
    $('.vcodepop').hide();
    $('.mask').hide();
});
$('#vcodeimg,.vcodepop .vcodeimgbox span').on('click', function () {
    // 在图片后面加上随机数换图片
    updateVocdeFun();
});

//
function clearCookie(cookieName) {
    document.cookie = cookieName + '=; expires=Thu, 01 Jan 1970 00:00:01 GMT;';
}

function setCookie(name, value, days) {
    var expires = "";
    if (days) {
        var date = new Date();
        date.setTime(date.getTime() + (days*24*60*60*1000));
        expires = "; expires=" + date.toUTCString();
    }
    document.cookie = name + "=" + (value || "")  + expires + "; path=/;domain=.creditchina.gov.cn";
    
}

function updateVocdeFun() {
  if(getCookie("cookienum")){

  }else{
    var cookienum =  Math.random();
    setCookie("cookienum", cookienum,1);
  }
    var ran = Math.random(); 
    $('#vcodeimg').attr('src', ''+config.VcodeIP+'verify/getVerify?_v=' + ran);
    $('#vcode').val('');
    $('.vcodepop input').css({
        'border':'1px solid #CCD3D9'
    });
}

function getCookie(cname)
{
  var name = cname + "=";
  var ca = document.cookie.split(';');
  for(var i=0; i<ca.length; i++) 
  {
    var c = ca[i].trim();
    if (c.indexOf(name)==0) return c.substring(name.length,c.length);
  }
  return "";
}




function setCookieExpireInOneMinute(name, value) {
    var date = new Date();
    date.setTime(date.getTime() + (5 * 60 * 1000)); // 1分钟
    var expires = "expires=" + date.toUTCString();
    document.cookie = name + "=" + value + ";" + expires + ";path=/";
}

// function validateCaptcha(custoumCallback) {
//     updateVocdeFun()
//     $('.vcodepop').show();
//     $('.mask').show();
//     $(".vcodepop .confirm").click(function () {
//         txt = $("#vcode").val();
//         $.ajax({
//             type: "post",
//             url: config.VcodeIP+"verify/checkVerify",
//             dataType: "json",
//             async: false,
//             data: {
//                 'verifyInput': txt
//             },
//             success: function (res) {

//                 if (res.code == 0) {
//                     // console.log(res.code,$('#vcode').val());
//                     setCookieExpireInOneMinute("vcode", "1");

//                     $('.vcodepop .vcodeinputbox p').hide();
//                     $('.vcodepop').hide();
//                     $('.mask').hide();
//                     console.log(getCookie('vcode'))
//                     //flag = true;
//                     custoumCallback();
//                 } else {
//                     updateVocdeFun()
//                     $('.vcodepop .vcodeinputbox p').html(res.msg).show()
//                 }

//             },
//             error: function (res) {
//                 // console.log(res.msg)
//                 updateVocdeFun()
//                 $('.vcodepop .vcodeinputbox p').html(res.msg).show()
//             }
//         });

//     });
// }

// function validateHuman(custoumCallback) {
//     console.log('govcode', getCookie('vcode'))
//     if (getCookie('vcode') == 1) {
//         console.log(getCookie('vcode'))
//         custoumCallback();
//     } else {
//         validateCaptcha(custoumCallback);
//     }

// };