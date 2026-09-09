param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("Compose", "UpdateFields", "RepairAndUpdateFields")]
    [string]$Mode,

    [Parameter(Mandatory = $true)]
    [string]$Output,

    [string]$NormalizedOutput,

    [string]$Source,
    [string]$Template,

    [ValidateSet("base", "custom", "tag", "cloud")]
    [string]$DocumentKind = "base"
)

$ErrorActionPreference = "Stop"

function Get-CleanParagraphText {
    param([object]$Paragraph)
    return ([string]$Paragraph.Range.Text -replace '[\x00-\x1F]', '').Trim()
}

function Find-SourceBodyRange {
    param(
        [object]$Document,
        [string]$Kind
    )

    $tocSeen = $false
    $start = $null
    for ($index = 1; $index -le $Document.Paragraphs.Count; $index++) {
        $paragraph = $Document.Paragraphs.Item($index)
        $text = Get-CleanParagraphText $paragraph
        if (-not $tocSeen) {
            if ($text -match '^SUM.RIO$') {
                $tocSeen = $true
            }
            continue
        }
        if ($Kind -eq "cloud") {
            if ($text.Length -lt 100 -and $text -match '^1\.\s*CONTROLE DE DOCUMENTO') {
                $start = [int]$paragraph.Range.Start
                break
            }
            continue
        }
        $styleName = ""
        try {
            $styleName = [string]$paragraph.Style.NameLocal
        }
        catch {
            $styleName = [string]$paragraph.Style
        }
        if ($text -and -not $styleName.StartsWith("TOC")) {
            $start = [int]$paragraph.Range.Start
            break
        }
    }
    if ($null -eq $start) {
        throw "Nao foi possivel localizar o inicio do conteudo apos o sumario."
    }

    $end = [int]$Document.Content.End - 1
    for ($index = 1; $index -le $Document.Paragraphs.Count; $index++) {
        $paragraph = $Document.Paragraphs.Item($index)
        if ([int]$paragraph.Range.Start -le $start) {
            continue
        }
        $text = Get-CleanParagraphText $paragraph
        if ($text -match '^SUA MELHOR ALIADA') {
            $end = [int]$paragraph.Range.Start
            break
        }
    }
    if ($end -le $start) {
        throw "O intervalo de conteudo do documento de origem e invalido."
    }

    # Word stores a section break at the end of the preceding section. When
    # FormattedText includes that final marker, the official three-section
    # shell gains an unintended fourth section. Keep the technical body, but
    # stop immediately before the first legacy section boundary after it.
    for ($sectionIndex = 2; $sectionIndex -le $Document.Sections.Count; $sectionIndex++) {
        $sectionStart = [int]$Document.Sections.Item($sectionIndex).Range.Start
        if ($sectionStart -gt $start -and $sectionStart -le $end) {
            $end = $sectionStart - 1
            break
        }
    }
    return $Document.Range($start, $end)
}

$word = $null
$sourceDocument = $null
$targetDocument = $null
try {
    $outputPath = [System.IO.Path]::GetFullPath($Output)
    $word = New-Object -ComObject Word.Application
    $word.Visible = $false
    $word.DisplayAlerts = 0
    $word.ScreenUpdating = $false
    foreach ($setting in @(
        "CheckSpellingAsYouType",
        "CheckGrammarAsYouType",
        "SuggestSpellingCorrections",
        "BackgroundSave",
        "Pagination",
        "UpdateLinksAtOpen",
        "SaveNormalPrompt"
    )) {
        try {
            $word.Options.$setting = $false
        }
        catch {
            # Some Word builds expose a subset of these performance options.
        }
    }

    if ($Mode -eq "UpdateFields") {
        if (-not [System.IO.File]::Exists($outputPath)) {
            throw "Documento para atualizacao de campos nao encontrado."
        }
        $targetDocument = $word.Documents.Open($outputPath, $false, $false, $false)
        for ($index = 1; $index -le $targetDocument.TablesOfContents.Count; $index++) {
            $targetDocument.TablesOfContents.Item($index).Update()
        }
        [void]$targetDocument.Fields.Update()
        $targetDocument.Save()
        exit 0
    }

    if ($Mode -eq "RepairAndUpdateFields") {
        if (-not [System.IO.File]::Exists($outputPath)) {
            throw "Documento composto para reparo nao encontrado."
        }
        if (-not $NormalizedOutput) {
            throw "NormalizedOutput e obrigatorio no modo RepairAndUpdateFields."
        }
        $normalizedPath = [System.IO.Path]::GetFullPath($NormalizedOutput)
        $normalizedDirectory = [System.IO.Path]::GetDirectoryName($normalizedPath)
        [System.IO.Directory]::CreateDirectory($normalizedDirectory) | Out-Null
        $missing = [Type]::Missing
        $targetDocument = $word.Documents.Open(
            $outputPath,
            $false,
            $false,
            $false,
            $missing,
            $missing,
            $false,
            $missing,
            $missing,
            $missing,
            $missing,
            $false,
            $true
        )
        try {
            $word.Options.Pagination = $true
        }
        catch {
            # Repaginate below remains the authoritative synchronous operation.
        }
        $targetDocument.Repaginate()
        if ($targetDocument.TablesOfContents.Count -lt 1) {
            throw "Campo de sumario nativo nao encontrado no documento composto."
        }
        $toc = $targetDocument.TablesOfContents.Item(1)
        [void]$toc.Update()
        $targetDocument.Repaginate()
        $toc = $targetDocument.TablesOfContents.Item(1)
        [void]$toc.UpdatePageNumbers()
        $targetDocument.SaveAs2($normalizedPath, 16)
        if (-not [System.IO.File]::Exists($normalizedPath)) {
            throw "O Word nao materializou o DOCX normalizado."
        }
        exit 0
    }

    if (-not $Source -or -not $Template) {
        throw "Source e Template sao obrigatorios no modo Compose."
    }
    $sourcePath = [System.IO.Path]::GetFullPath($Source)
    $templatePath = [System.IO.Path]::GetFullPath($Template)
    if (-not [System.IO.File]::Exists($sourcePath)) {
        throw "Documento de origem nao encontrado."
    }
    if (-not [System.IO.File]::Exists($templatePath)) {
        throw "Template oficial nao encontrado."
    }
    $outputDirectory = [System.IO.Path]::GetDirectoryName($outputPath)
    [System.IO.Directory]::CreateDirectory($outputDirectory) | Out-Null
    [System.IO.File]::Copy($templatePath, $outputPath, $true)

    $sourceDocument = $word.Documents.Open($sourcePath, $false, $true, $false)
    $sourceRange = Find-SourceBodyRange $sourceDocument $DocumentKind
    $targetDocument = $word.Documents.Open($outputPath, $false, $false, $false)
    if ($targetDocument.Sections.Count -ne 3) {
        throw "O template oficial precisa conter exatamente tres secoes."
    }
    $targetBody = $targetDocument.Sections.Item(2).Range
    $targetBody.End = [int]$targetBody.End - 1
    $targetBody.FormattedText = $sourceRange.FormattedText
    $targetDocument.Save()
}
finally {
    if ($null -ne $targetDocument) {
        $targetDocument.Close(0)
    }
    if ($null -ne $sourceDocument) {
        $sourceDocument.Close(0)
    }
    if ($null -ne $word) {
        $word.Quit()
    }
    [System.GC]::Collect()
    [System.GC]::WaitForPendingFinalizers()
}
