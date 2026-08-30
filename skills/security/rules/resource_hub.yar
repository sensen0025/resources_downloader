/*
 * Resource Hub — 下载安全查毒技能内置 YARA 规则
 * 关注爬取资源的典型恶意载荷:伪装下载器、编码 PowerShell、VBS/JS 投毒、
 * 双重扩展名、可执行文件伪装图片/文档。
 * meta.severity: high → 判 infected;medium/low → 判 suspicious。
 * 用户可在 SECURITY_YARA_RULES_DIR 追加自己的规则文件。
 */

rule Susp_JS_Downloader
{
    meta:
        description = "JavaScript 下载器:ActiveX/ADODB 下载并落地可执行载荷"
        severity = "high"
        author = "resource-hub security skill"
    strings:
        $a = 'MSXML2.XMLHTTP' ascii wide nocase
        $b = 'ADODB.Stream' ascii wide nocase
        $c = 'WScript.Shell' ascii wide nocase
        $d = '.exe' ascii wide nocase
        $e = 'document.write' ascii wide nocase
    condition:
        filesize < 2MB and any of ($a,$b,$c) and $d and $e
}

rule Susp_PowerShell_Encoded
{
    meta:
        description = "PowerShell 编码命令 / 远程下载执行(常见投毒入口)"
        severity = "high"
        author = "resource-hub security skill"
    strings:
        $a = '-EncodedCommand' ascii wide nocase
        $b = 'FromBase64String' ascii wide nocase
        $c = 'Invoke-Expression' ascii wide nocase
        $d = 'DownloadString' ascii wide nocase
        $e = 'DownloadFile' ascii wide nocase
        $f = 'New-Object Net.WebClient' ascii wide nocase
    condition:
        filesize < 2MB and (any of ($a,$b,$c) or (any of ($d,$e) and $f))
}

rule Susp_VBScript_Dropper
{
    meta:
        description = "VBS 脚本:创建 Shell / 下载落地载荷"
        severity = "high"
        author = "resource-hub security skill"
    strings:
        $a = 'WScript.Shell' ascii wide nocase
        $b = 'MSXML2.XMLHTTP' ascii wide nocase
        $c = 'ADODB.Stream' ascii wide nocase
        $d = 'Shell.Run' ascii wide nocase
        $e = 'CreateObject(' ascii wide nocase
    condition:
        filesize < 2MB and $e and (($a and any of ($b,$c)) or $d)
}

rule Susp_Certutil_Bitsadmin
{
    meta:
        description = "certutil/bitsadmin 下载解码(Windows 系统工具滥用)"
        severity = "high"
        author = "resource-hub security skill"
    strings:
        $a = 'certutil' ascii wide nocase
        $b = '-urlcache' ascii wide nocase
        $c = '-decode' ascii wide nocase
        $d = 'bitsadmin' ascii wide nocase
        $e = '/transfer' ascii wide nocase
    condition:
        filesize < 2MB and (($a and any of ($b,$c)) or ($d and $e))
}

rule Susp_Double_Extension
{
    meta:
        description = "双重扩展名伪装(photo.jpg.exe)"
        severity = "medium"
        author = "resource-hub security skill"
    condition:
        filesize < 50MB and filename matches
        /\.(jpg|jpeg|png|gif|bmp|webp|pdf|doc|docx|xls|xlsx|zip|rar|7z|txt|litematic|schematic)\.[a-z0-9]{2,5}$/i
}

rule Susp_PE_In_Disguise
{
    meta:
        description = "PE 可执行文件但扩展名伪装为图片/文档/投影"
        severity = "high"
        author = "resource-hub security skill"
    condition:
        uint16(0) == 0x5A4D and filesize < 200MB and filename matches
        /\.(jpg|jpeg|png|gif|bmp|webp|pdf|txt|doc|docx|litematic|schematic)$/i
}

rule Susp_ELF_In_Disguise
{
    meta:
        description = "ELF 可执行文件但扩展名伪装为图片/文档"
        severity = "high"
        author = "resource-hub security skill"
    condition:
        uint32(0) == 0x464C457F and filesize < 200MB and filename matches
        /\.(jpg|jpeg|png|gif|bmp|webp|pdf|txt|doc|docx)$/i
}
