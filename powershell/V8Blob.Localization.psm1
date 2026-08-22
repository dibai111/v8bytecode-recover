Set-StrictMode -Version Latest

$script:V8Language = 'zh-TW'
$script:V8Strings = @{
    'zh-TW' = @{
        Title = 'v8bytecode-recover PowerShell 工具'
        QuickRecover = '快速還原（自動偵測及合理預設值）'
        Inspect = '檢查 blob、V8 profile 及 snapshot'
        ExportAnalysis = '匯出一項分析結果'
        Profiles = '列出或驗證內置 V8 profiles'
        Benchmark = '比較 backend 效能'
        Doctor = '檢查執行環境'
        Exit = '離開'
        SelectFeature = '選擇功能'
        InvalidSelection = '選擇無效。'
        PressEnter = '按 Enter 返回主選單'
        InputPath = '輸入檔案或目錄路徑'
        PathMissing = '路徑不存在，請重新輸入。'
        OutputPath = '輸出目錄 [按 Enter 使用 {0}]'
        AnalysisOutput = '分析輸出'
        ProfileAction = 'Profile 操作'
        ProfileVersion = 'V8 版本 [按 Enter 使用所有內置 profiles]'
        ProfileOutput = '輸出目錄 [按 Enter 使用 {0}]'
        Backends = 'Backend（profile 或 profile,d8）[預設 profile]'
        ChooseOne = '請選擇以下其中一項：{0}'
        ChoiceHint = '{0} [{1}，預設 {2}]'
        Completed = '操作完成。'
        Finished = '操作完成，結束代碼：{0}'
    }
    'zh-CN' = @{
        Title = 'v8bytecode-recover PowerShell 工具'
        QuickRecover = '快速还原（自动检测及合理默认值）'
        Inspect = '检查 blob、V8 profile 及 snapshot'
        ExportAnalysis = '导出一项分析结果'
        Profiles = '列出或验证内置 V8 profiles'
        Benchmark = '比较 backend 性能'
        Doctor = '检查运行环境'
        Exit = '退出'
        SelectFeature = '选择功能'
        InvalidSelection = '选择无效。'
        PressEnter = '按 Enter 返回主菜单'
        InputPath = '输入文件或目录路径'
        PathMissing = '路径不存在，请重新输入。'
        OutputPath = '输出目录 [按 Enter 使用 {0}]'
        AnalysisOutput = '分析输出'
        ProfileAction = 'Profile 操作'
        ProfileVersion = 'V8 版本 [按 Enter 使用所有内置 profiles]'
        ProfileOutput = '输出目录 [按 Enter 使用 {0}]'
        Backends = 'Backend（profile 或 profile,d8）[默认 profile]'
        ChooseOne = '请选择以下其中一项：{0}'
        ChoiceHint = '{0} [{1}，默认 {2}]'
        Completed = '操作完成。'
        Finished = '操作完成，结束代码：{0}'
    }
}

function Set-V8Language {
    param(
        [ValidateSet('zh-TW', 'zh-CN')]
        [string]$Language = 'zh-TW'
    )

    $script:V8Language = $Language
}

function Get-V8Text {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Key,

        [AllowEmptyCollection()]
        [object[]]$Arguments = @()
    )

    $value = $script:V8Strings[$script:V8Language][$Key]
    if ($null -eq $value) {
        throw "Unknown localization key: $Key"
    }
    if ($Arguments.Count -eq 0) { return $value }
    return ($value -f $Arguments)
}

Export-ModuleMember -Function Get-V8Text, Set-V8Language
