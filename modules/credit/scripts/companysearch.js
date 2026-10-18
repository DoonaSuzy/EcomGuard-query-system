var liNum = 0;

// 信用信息  关键字段  creditInfo
var keyword = "";
var Request = new Object();
Request = GetRequest();
var keyword = Request.keyword;
var scenes = Request.scenes;
var tableName = Request.tableName;
var searchState = Request.searchState;
var entityType = Request.entityType;
var httpUrl = {
    // serachUrl: config.IP+"credit_info_search",
    // historyUrl: config.IP+"select_browse_history"
    serachUrl: config.IP + "catalogSearchHome",
    historyUrl: config.IP + "select_browse_history"
}
/* 
 全局变量
 */
window.selfObj = {
    searchPram: {
        keyword: keyword || "",//
        scenes: scenes || "",//
        tableName: tableName || "",//
        searchState: searchState || "",//
        entityType: entityType || "",//
        templateid: "", //地域
        page: 1,//=（请求页面，默认为1）&
        pageSize: 10,//=（页面大小，默认为10）&
        //exact:1,//=（是否为精确查询，大于0位精确查询）
    },
    currentPage: 1,
    paginationEvent: function (pageNumber) {


        //验证码
        if (vcodeswitch == 1) {
            if (getCookie('vcode') == '') {
                updateVocdeFun();
                $('.vcodepop').show();
                $('.mask').show();
                $(".vcodepop .confirm").off('click').on('click', function () {
                    txt = $("#vcode").val();
                    if(txt==''){
                        $('.vcodepop .vcodeinputbox p').html('请输入验证码！').show();
                        $('.vcodepop input').css({
                                'border': '1px solid #d90000'
                            });
                    }else{
                        $.ajax({
                            type: "post",
                            url: config.VcodeIP + "verify/checkVerify",
                            xhrFields: {
                                withCredentials: true,
                            },
                            dataType: "json",
                            async: false,
                            data: {
                                'verifyInput': txt
                            },
                            success: function (res) {
    
                                if (res.code == 0) {
                                    setCookieExpireInOneMinute("vcode", "1");
    
                                    $('.vcodepop .vcodeinputbox p').hide();
                                    $('.vcodepop').hide();
                                    $('.mask').hide();
    
                                    searchAjax(pageNumber)
    
    
    
                                }
                                else {
                                    updateVocdeFun()
                                    $('.vcodepop .vcodeinputbox p').html(res.msg).show();
                                    $('.vcodepop input').css({
                                        'border': '1px solid #d90000'
                                    });
                                }
    
                            },
                            error: function (res) {
                                updateVocdeFun()
                                $('.vcodepop .vcodeinputbox p').html(res.msg).show();
                                $('.vcodepop input').css({
                                    'border': '1px solid #d90000'
                                });
                            }
                        });
                    }
                    

                });
            } else {

                searchAjax(pageNumber)

            }
        } else {

            searchAjax(pageNumber)

        }



    }
}
/*
 初始化
 */
function searchAjax(pageNumber) {
    var param = {
        keyword: selfObj.searchPram.keyword,
        scenes: selfObj.searchPram.scenes,//
        tableName: selfObj.searchPram.tableName,//
        searchState: selfObj.searchPram.searchState,//
        entityType: selfObj.searchPram.entityType,//
        templateId: selfObj.searchPram.templateid,
        page: pageNumber,
        pageSize: selfObj.searchPram.pageSize,
    }
    jQuery.ajax({
        url: httpUrl.serachUrl,
        type: 'GET',
        xhrFields: {
            withCredentials: true,
        },
        data: param,
        dataType: 'json',
        success: function (data) {
            if (data.data.total <= 0) {
                $(".search-result-box").hide();
                $(".no-data").show();
             //   $('.yysstip').show();
                $("#search-tip-span2").html(0);
                return
            } else {
                $(".search-result-box").show();
                $(".no-data").hide();
             //   $('.yysstip').hide();
            }
            // 初始化页面
            $('#pagination').pagination({
                pages: data.data.totalSize,
                currentPage: data.data.page,
                onPageClick: function (num) {
                    selfObj.paginationEvent(num);
                }

            });
            if (data.data.totalSize < 1) {
                $('#pagination').hide();
            }
            //绑定数据
            $("#search-tip-span2").html(data.data.total);
            var html = serachHtml(data.data);
            $("#companylists").empty();
            $("#companylists").html(html);
        },
        error: function (error) {
            $(".search-result-box").hide();
            $(".no-data").show();
         //   $('.yysstip').show();
        },
    });
}


//  浏览记录查询
browseHistoryfn();
//  初始化   
selfObj.paginationEvent(1);





//  筛选的功能
$(".select-li3 .select-li3-list li").click(function () {
    if ($(this).index() == 0) {
        liNum = 2;
    } else if ($(this).index() == 1) {
        liNum = 4;
    } else if ($(this).index() == 2) {
        liNum = 5;
    } else if ($(this).index() == 3) {
        liNum = 4;
    }

    console.log(liNum);
    if ($(this).data('searchstate') != 2) {
        $('.nav-position').html('您所在的位置：<a href="/" title="首页" class="CurrChnlCls">首页</a>&nbsp;&gt;&nbsp;<a href="' + document.location + '" title="信用信息查询" class="CurrChnlCls">信用信息查询</a>&nbsp;&gt;&nbsp;<a title="' + $(this).text() + '" class="CurrChnlCls" style="color: rgb(0, 99, 186);">' + $(this).text() + '</a>')
        document.title = $(this).text() + '_信用信息查询-信用中国'
        document.getElementsByName('keywords')[0].content = $(this).text() + '查询,信用中国,信用'
        document.getElementsByName('description')[0].content = '信用中国以国家发展改革委、中国人民银行指导，国家公共信用信息中心主办，为您提供{信用类型}查询功能，查' + $(this).text() + '，就上信用中国官网。'
    } else {
        $('.nav-position').html('您所在的位置：<a href="/" title="首页" class="CurrChnlCls">首页</a>&nbsp;&gt;&nbsp;<a href="' + document.location + '" title="信用信息查询" class="CurrChnlCls" style="color: rgb(0, 99, 186);">信用信息查询</a>')
        document.title = '信用信息查询-信用中国';
        document.getElementsByName('keywords')[0].content = '信用信息查询,信用中国,信用'
        document.getElementsByName('description')[0].content = '信用中国以国家发展改革委、中国人民银行指导，国家公共信用信息中心主办，为您提供{信用类型}查询功能，查信用信息，就上信用中国官网。'
    }


    $(this).addClass("selected").siblings().removeClass("selected");
    if ($(this).hasClass("select-all")) {
        $("#selectB").remove();
    } else {
        var copyThisB = $(this).clone();
        if ($("#selectB").length > 0) {
            $("#selectB a").html($(this).text());
        } else {
            $(".conditionQuery .select .select-li1 .select-li1-list").append(copyThisB.attr("id", "selectB"));
        }
    }
    var val = $(this).data("templateid");
    var tablenameval = $(this).data("tablename");
    var searchState = $(this).data("searchstate");
    selfObj.searchPram.templateid = val;
    selfObj.searchPram.tableName = tablenameval;
    selfObj.searchPram.searchState = searchState;
    selfObj.paginationEvent(1);
});
// 点击跳转 记录浏览记录
$(".companylists").on("click", ".company-item", function () {
    var message = $(this).data("message");
    console.log(message);
    console.log(message.accurate_entity_name_query)
    var obj = { name: message.accurate_entity_name_query, entityType: message.entityType };

    if (message.uuid) {
        obj.uuid = message.uuid;
    }
    if (message.accurate_entity_code) {
        obj.tyshxydm = message.accurate_entity_code;
    }

    var browseHistory = JSON.parse($.cookie('browseHistory') ? $.cookie('browseHistory') : "[]");
    browseHistory.unshift(obj);
    if (browseHistory.length > 6) {
        browseHistory = browseHistory.slice(0, 7);
    }
    $.cookie('browseHistory', JSON.stringify(browseHistory), { path: "/" });
    browseHistoryfn();
    // var url = config.FEIP+"xinyongxinxixiangqing/xyDetail.html?searchState=1&entityType="+encodeURIComponent(message.entityType)+"&keyword="+encodeURIComponent(message.accurate_entity_name_query)+"&uuid="+encodeURIComponent(message.uuid)+"&tyshxydm="+encodeURIComponent(message.accurate_entity_code);
    var url = config.FEIP + "xinyongxinxixiangqing/xyDetail.html?searchState=1&entityType=" + encodeURIComponent(message.entityType) + "&keyword=" + encodeURIComponent(message.accurate_entity_name_query);
    if (message.uuid) {
        url += "&uuid=" + encodeURIComponent(message.uuid);
    }
    if (message.accurate_entity_code) {
        url += "&tyshxydm=" + encodeURIComponent(message.accurate_entity_code);
    }
    if (message.tagNum) {
        url += "&tagNum=" + encodeURIComponent(message.tagNum);
    }

    window.open(url, "_blank");
})
//清空浏览记录
$(".HistoryClearBtn").on("click", function () {
    $.cookie('browseHistory', "", { path: "/" });
    browseHistoryfn();
    $(".History_column_list").empty();
    $(".History_column_list").removeClass("History_column_list-height")
})

/**
 * [browseHistory  历史浏览记录]
 *
 * @param  {arr} data   [渲染的数据列表]
 * @return {string}       [字符串模板]
 */
function browseHistoryfn() {
    var browseHistory = $.cookie('browseHistory') || "";
    if (browseHistory.length > 0) {
        browseHistory = JSON.parse(browseHistory);
        var html = "";
        $.each(browseHistory, function (k, v) {
            var str = '';
            str += '<li class="duck_grey"><a target="_blank" href="../xinyongxinxixiangqing/xyDetail.html?searchState=1&entityType=' + encodeURIComponent(v.entityType) + '&keyword=' + encodeURIComponent(v.name)
            if (v.uuid) {
                str += '&uuid=' + v.uuid
            }
            if (v.tyshxydm) {
                str += '&tyshxydm=' + v.tyshxydm
            }
            str += '">' + v.name + '</a></li>'


            html += str;
        })
        $(".no-histroy").removeClass("show").addClass("hide");
        $(".History_column_list").removeClass("History_column_list-height").addClass("History_column_list-height");
    } else {
        $(".History_column_list").removeClass("History_column_list-height")
        $(".no-histroy").removeClass("hide").addClass("show");
        return
    }
    $(".History_column_list").html(html);
}


function isempty(data) {
    return data ? data : "— —";
}

/**
 * [serachHtml 渲染模板]
 *
 * @param  {arr} data   [渲染的数据列表]
 * @return {string}       [字符串模板]
 */
function serachHtml(dataList) {
    var data = dataList.list;
    var len = data.length;
    var html = "";
    var dataSource = "";
    var title = "";
    var title2 = "";
    var str = "";
    // 
    for (var i = 0; i < len; i++) {
        var title = keywrodColor(data[i].accurate_entity_name_query, selfObj.searchPram.keyword)
        dataSource = data[i].dataSource;
        // if (dataList.entityType == 3)
        //  {
        //         if (dataSource == "中央编办")
        //         {
        //         	   title = data[i].dymc;
        //         	   if (data[i].demc !="" && data[i].demc != null)
        // 							title += "("+data[i].demc+")";
        //         }
        //  }else{
        //     title = data[i].jgmc;

        // }
        var str = "";
        //  str+='<li class="company-item”  class="message-cookie'+i+'"  data-title=\''+data[i].accurate_entity_name_query+'\' data-message=\''+config.FEIP+'xinyongxinxixiangqing/xyDetail.html?searchState=1&entityType='+data[i].entityType+'&keyword='+data[i].accurate_entity_name_query+'\' data-entitytype=\''+data[i].entityType+'\'>'
        var messages = data[i];
        //messages.
        messages["tagNum"] = liNum;



        str += '<li class="company-item  class="message-cookie' + i + '"  data-message=\'' + JSON.stringify(messages) + '\'>'
        str += '<p class="company-name">' + title + '</p>'
        str += '<p class="company-messages">'
        str += '<span>统一社会信用代码：</span>'
        str += '<span class="company-number">' + isempty(data[i].accurate_entity_code) + '</span>'
        str += '<span style="padding-left:15px;">主体类型：</span>'
        if (data[i].entityType == 1) {
            str += '<span class="company-number">企业法人</span>'
        } else if (data[i].entityType == 2) {
            str += '<span class="company-number">个体工商</span>'
        } else if (data[i].entityType == 3) {
            str += '<span class="company-number">党群机关</span>'
        } else if (data[i].entityType == 4) {
            str += '<span class="company-number">自然人</span>'
        } else if (data[i].entityType == 5) {
            str += '<span class="company-number">社团法人</span>'
        } else if (data[i].entityType == 6) {
            str += '<span class="company-number">车牌</span>'
        } else if (data[i].entityType == 7) {
            str += '<span class="company-number">事业单位</span>'
        } else if (data[i].entityType == 8) {
            str += '<span class="company-number">编办直接管理机构编制的群众团体</span>'
        }

        str += '</p>'
        str += '</li>'
        html += str;
    }
    return html;
}
/**
 * [keywrodColor 关键词着色替换]
 * @param  {string} str     [需要替换的字符串]
 * @param  {string} key     [关键字]
 * @param  {string} color   [颜色 默认 #0063ba]
 * @return {string}         [替换完成的字符串]
 */
function keywrodColor(str, key, color) {
    if (key) {
        var sText = str,
            color = color || "#0063ba",
            sKey = "<span style='color: " + color + ";'>" + key + "</span>",
            rStr = new RegExp(key, "g");

        var rText = sText.replace(rStr, sKey); //替换key



    } else {
        var rText = str;
    }
    return rText
}

/**
 *   获取 url  ？后 的字符串
 */
function GetRequest() {
    var url = location.search;
    var theRequest = new Object();
    if (url.indexOf('?') != -1) {
        var str = url.substr(1);
        strs = str.split('&');
        for (var i = 0; i < strs.length; i++) {
            theRequest[strs[i].split('=')[0]] = decodeURIComponent(strs[i].split('=')[1]);
        }
    }
    return theRequest;
}


/*******************输入页数 查询相应的结果***************************/
$(function () {

    /*******************输入页数 查询相应的结果***************************/
    $('.pagination').on('click', '.page-btn', function () {
        var pageNum = parseInt($(this).parent().prev().find('input').val());
        pageNum && $(this).parents('.pagination').pagination('selectPage', pageNum);
    });
});