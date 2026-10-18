function hasClass(obj, cls) {
    if (obj.className) {
        return obj.className.match(new RegExp('(\\s|^)' + cls + '(\\s|$)'));
    } else {
        return false;
    }

}

function addClass(obj, cls) {
    if (!this.hasClass(obj, cls)) {
        obj.className += " " + cls;
    }
}

function removeClass(obj, cls) {
    if (hasClass(obj, cls)) {
        var reg = new RegExp('(\\s|^)' + cls + '(\\s|$)');
        obj.className = obj.className.replace(reg, ' ');
    }
}

(function () {
    //导航下拉
    // $('.new_nav > .flex > li').hover(function () {
    // 	if($(this).index()==2 || $(this).index()==6)

    // 	$('.new_nav').css({
    // 		'height': '113px'
    // 	})
    // }, function () {
    // 	$('.new_nav').css({
    // 		'height': '80px'
    // 	})
    // });

    //获取url中"?"符后的字串
function GetRequest() {
    var url = location.search; //获取url中"?"符后的字串
    var theRequest = new Object();
    if (url.indexOf("?") != -1) {
        var str = url.substr(1);
        strs = str.split("&");
        for (var i = 0; i < strs.length; i++) {
            theRequest[strs[i].split("=")[0]] = (strs[i].split("=")[1]);
        }
    }
    return theRequest;

    
}
var params = GetRequest();
        // if (params['index']) {
        //     $('.search_term').find('span')[parseInt(params['index'])].click();
        // }
        if (params['keyword']){
            $('.new_search_text').find('.search_input').val(decodeURI(params['keyword']));
        }


    var searchItem = parseInt(params['index']);
    if (isNaN(searchItem)){
       searchItem = 0;
    }

    //头部搜索下拉
    $('.new_option').on('keyup', 'sapn', function (e) {
        var keycode = e.keyCode || e.which
        if (keycode == 9) {
            $(this).trigger('click')
        }
    })
    $('.new_option').on('click', 'span', function () {
        if ($(this).hasClass('arrup')) {
            $(this).removeClass('arrup');
        } else {
            $(this).addClass('arrup');
        }
        $(this).siblings('ul').toggle();
        $(this).parents('.new_option').siblings().find('ul').hide();
        $(this).parents('.new_option').siblings().find('span').removeClass('arrup');

    });

    //头部搜索
    $('.new_option li').click(function () {
        var t = $(this).text();
        $(this).parents('.new_option').find('span').text(t);
        searchItem = $(this).index();
        $(this).parent('.new_option ul').hide();
        var supportPlaceholder = 'placeholder' in document.createElement('input');
        var update = false;
        if (!supportPlaceholder) {
            update = ($('#search_input').attr("placeholder") === $('#search_input').val() || $('#search_input').val() === "");
        }
        switch (searchItem) {
            case 0:
                document.getElementById("search_input").setAttribute("placeholder", "请输入主体名称或者统一社会信用代码");

                if (!supportPlaceholder && $('#search_input').attr("placeholder") != $('#search_input').val()) {
                    if (update)
                        $('#search_input').val("请输入主体名称或者统一社会信用代码")
                }
                break;
            case 1:
                document.getElementById("search_input").setAttribute("placeholder", "请输入文章关键词");

                if (!supportPlaceholder && $('#search_input').attr("placeholder") != $('#search_input').val()) {
                    if (update)
                        $('#search_input').val("请输入文章关键词")
                }
                break;
            default:
                break;
        }
    });


    //头部搜索跳转
    $(".search_btn").bind('click', function () {
        console.log(searchItem)
        var url;
        var val = $('#search_input').val();
        if (!val.toString() || val.toString() == "") {
            M.dialog = jqueryAlert({
                'style': 'pc',
                'title': '提示',
                'content': "请输入关键字",
                'modal': true,
                'contentTextAlign': 'center',
                'animateType': 'scale',
                'bodyScroll': 'true',
                'buttons': {
                    '关闭': function () {
                        M.dialog.close();
                    }
                }
            });
            $('#search_input').blur();
            return
        } else {
            switch (searchItem) {
                case 0:
                    url = config.FEIP + 'xinyongxinxi/index.html?index=0&scenes=defaultScenario&tableName=credit_xyzx_tyshxydm&searchState=2&entityType=1,2,4,5,6,7,8&keyword=' + encodeURI($('#search_input').val());
                    window.open(url);
                    break;
                case 1:
                    url = config.FEIP + 'articesearch/index.html?index=1&keyword=' + encodeURI($('#search_input').val());
                    window.open(url);
                    break;
            }
        }

    });
    // 绑定enter
    $('#search_input').keydown(function (e) {
        if (e.keyCode === 13) {
            $(".search_btn").trigger('click');
        }
    });


    var M = {}; // alert 自定义弹窗


    $(document).bind("click", function (e) {
        var target = $(e.target);
        if (target.closest(".tit").length == 0) {
            $('.option div').hide();
            $('.tit').removeClass('arrup')

        }
    })
    $('.new_xglink').on('keyup', '.tit', function (e) {
        var keycode = e.keyCode || e.which
        if (keycode == 9) {
            $(this).trigger('click')
        }
    })
    $('.new_xglink').on('click', '.tit', function () {
        if ($(this).hasClass('arrup')) {
            $(this).removeClass('arrup');
        } else {
            $(this).addClass('arrup');
        }
        $(this).siblings('div').toggle();

        $(this).parents('.option').siblings().find('div').hide();
        $(this).parents('.option').siblings().find('a').removeClass('arrup');

    }).on('click', '.option li', function () {

        var t = $(this).text();
        // $(this).parents('.option').find('.tit').text(t)
        $(this).parent('.option div').hide();

    });
    $('.option li:last').find('a').blur(function () {
        $(this).parents('div.divbox').hide();
        $(this).parents('div.divbox').siblings('a').removeClass('arrup')
    });

})()





/**外部链接 跳转时的弹窗*/
var alertObj = {
    isShow: true,
    outsideAlert: function (href, preHref) {
        var cssStyle = '<style>' +
            'body{box-sizing:border-box;}#outsideAlert {display:none;position: fixed;top: 0;left: 0; bottom: 0;right: 0;z-index:999;width: 100%;height: 100%;overflow:hidden}' +
            '#outsideAlert .alert-dialog {width: 482px;height: 320px;margin: 150px auto 0;border-radius: 10px;' +
            'overflow: hidden;}#outsideAlert .alert-dialog .alert-content {position: relative;width: 100%;overflow: hidden;' +
            'height: 100%;background-color: #fff;padding: 20px 20px 30px;text-align: center;box-sizing: border-box;}' +
            '#outsideAlert .alert-dialog .alert-content .alert-head {height: 80px;line-height: 80px;}' +
            '#outsideAlert .alert-dialog .alert-content .alert-head h4 {color: #0063ba;font-size: 24px;}' +
            '#outsideAlert .alert-dialog .alert-content .alert-head .alert-close {position: absolute;top: 18px;' +
            'right: 18px;width: 20px;height: 20px;line-height: 15px;color: #ea3e3e;font-size: 30px;' +
            'text-align: center;}#outsideAlert .alert-dialog .alert-content .alert-body {font-size: 16px;' +
            'line-height: 30px;}#outsideAlert .alert-dialog .alert-content .alert-body p {color: #333;}' +
            '#outsideAlert .alert-dialog .alert-content .alert-body span {color: #0063ba;display: inline-block;' +
            'width: 100%;}#outsideAlert .alert-dialog .alert-content .alert-foot {margin-top: 30px;}' +
            '#outsideAlert .alert-dialog .alert-content .alert-foot a {display: inline-block;width: 100px;' +
            'height: 30px;line-height: 30px;color: #fff;background-color: #0063ba;border-radius: 15px;' +
            'font-size: 16px;}#outsideAlert .alert-dialog .alert-content .alert-foot div {margin-top: 15px;}' +
            '#outsideAlert .alert-dialog .alert-content .alert-foot div input {width: 18px;height: 18px;' +
            '-webkit-appearance: checkbox; vertical-align: text-top;}#outsideAlert .alert-dialog .alert-content' +
            ' .alert-foot div label {font-size: 18px;color: #999;}.alertCover{position:fixed;width:100%;z-index:500;' +
            'height:100%;top:0;left:0;right:0;bottom:0;background-color:#000;opacity:0.3;-ms-filter: progid:DXImageTransform.Microsoft.Alpha(Opacity=30);filter: progid:DXImageTransform.Microsoft.Alpha(Opacity=30);;}' +
            '.bodyPadding{padding-right:17px;overflow:hidden;}</style>';
        var html = '<div id="outsideAlert" class="alert-box"><div class="alert-dialog"><div class="alert-content">' +
            '<div class="alert-head"><h4>温馨提示</h4><span class="alert-close" onclick="alertObj.closeLink(event)">&times;</span></div>' +
            '<div class="alert-body"><p>您将要链接的地址</p><span _target="' + href + '">（' + preHref + '）</span>' +
            '<p>已离开信用中国网站</p></div><div class="alert-foot"><a href="javascript:;" onclick="alertObj.openLink(event)">确 定</a>' +
            '<div><input id="checkBox" type="checkbox" /><label for="checkBox">不再显示该对话框</label></div>' +
            '</div></div></div></div><div class="alertCover"></div>';
        $('head').append(cssStyle);
        $('body').append(html);
    },
    closeLink: function () {
        $('#outsideAlert').fadeOut(100);
        $('.alertCover').fadeOut(100);
        $('body').removeClass('bodyPadding');
    },
    openLink: function () {
        var href = $('#outsideAlert').find('.alert-body span').attr('_target');
        if ($('#checkBox').prop('checked')) {
            alertObj.isShow = false;
        }
        $('#outsideAlert').fadeOut(100);
        $('.alertCover').fadeOut(100);
        $('body').removeClass('bodyPadding');
        window.open(href, '_blank', 'noopener=yes,noreferrer=yes');
    },
    setHref: function (href) {
        var arr1 = href.split('?'),
            arr2 = arr1[0].split('/');
        return arr2[0] + '//' + arr2[2];
    }

};
$(function () {
    $('body').on('click', 'a', function (e) {
        var href = $(this).attr('href');
        if (/(http?):/.test(href) || /(https?):/.test(href)) {
            var origin = window.location.origin;
            var preHref = alertObj.setHref(href);
            if (preHref != origin && preHref != 'https://creditcity.creditchina.gov.cn' && preHref != 'https://hmd.creditchina.gov.cn' && preHref != 'https://public.creditchina.gov.cn' && preHref != 'https://www.creditchina.gov.cn' && preHref != 'http://public.creditchina.gov.cn' && preHref != 'http://www.creditchina.gov.cn' && preHref != 'https://elearning.creditchina.gov.cn/' && preHref != "http://dev.admin.creditchina.com" && preHref != "https://admin.creditchina.gov.cn" && preHref != "https://user.creditchina.gov.cn" && preHref != "https://xyd.creditchina.gov.cn" && preHref != "https://zscq.creditchina.gov.cn") {
                e.preventDefault();
                if ($('#outsideAlert').length) {
                    if (!alertObj.isShow) {
                        window.open(href, '_blank');
                    } else {
                        $('#outsideAlert').find('.alert-body span').text('(' + preHref + ')').attr('_target', href);
                        $('#outsideAlert').fadeIn();
                        $('.alertCover').fadeIn();
                        $('body').addClass('bodyPadding');
                    }
                } else {
                    alertObj.outsideAlert(href, preHref);
                    $('#outsideAlert').fadeIn();
                    $('.alertCover').fadeIn();
                    $('body').addClass('bodyPadding');
                }
            } else {
                //alert('sdfs')
            }
        }
    });
});
/**搜索关键字 风险过滤*/
function filterWords(val) {
    var newVal = val.replace(/<.*?>(.*?)<\/.*?>/g, '').replace(/<.*?\/>/g, '').replace(/src[\r\n]*=[\r\n]*\\\'(.*?)\\\'/g, '').replace(/src[\r\n]*=[\r\n]*\\\"(.*?)\\\"/g, '').replace(/eval\\((.*?)\\)/g, '').replace(/e­xpression\\((.*?)\\)/g, '').replace(/javascript:/g, '').replace(/onload(.*?)=/g, '');
    return newVal;
}