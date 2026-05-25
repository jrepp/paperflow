%% research-radar.cls
%% Magazine-style periodical for arXiv research radar
%% Two-column layouts, paper cards, pull quotes, visual hierarchy
%%
\NeedsTeXFormat{LaTeX2e}
\ProvidesClass{research-radar}[2026/04/24 Research Radar Periodical]

\LoadClass[10pt,oneside,twocolumn]{article}

%% =============================================================================
%% PACKAGES
%% =============================================================================

\RequirePackage[utf8]{inputenc}
\RequirePackage[T1]{fontenc}
\RequirePackage{lmodern}
\RequirePackage[
    paperwidth=8.5in,
    paperheight=11in,
    margin=0.6in,
    columnsep=0.3in,
    headheight=20pt
]{geometry}

\RequirePackage[table]{xcolor}
\RequirePackage{graphicx}
\RequirePackage{fancyhdr}
\RequirePackage{booktabs}
\RequirePackage{parskip}
\RequirePackage{array}
\RequirePackage{colortbl}
\RequirePackage{caption}
\RequirePackage{multicol}
\RequirePackage{ragged2e}

\RequirePackage{tikz}
\usetikzlibrary{calc}

\RequirePackage{titlesec}
\RequirePackage{titletoc}

%% =============================================================================
%% COLOR PALETTE
%% =============================================================================

\definecolor{darkgray}{RGB}{38,38,38}
\definecolor{mediumgray}{RGB}{110,110,110}
\definecolor{lightgray}{RGB}{243,243,243}
\definecolor{rulegray}{RGB}{200,200,200}
\definecolor{accentblue}{RGB}{0,90,156}
\definecolor{accentgreen}{RGB}{34,139,80}
\definecolor{accentorange}{RGB}{210,120,20}
\definecolor{accentred}{RGB}{175,50,50}
\definecolor{coverbg}{RGB}{22,33,62}
\definecolor{coveraccent}{RGB}{255,193,7}
\definecolor{cardbg}{RGB}{250,250,252}
\definecolor{cardborder}{RGB}{220,222,228}

\RequirePackage{hyperref}
\hypersetup{
    colorlinks=true,
    linkcolor=darkgray,
    urlcolor=accentblue,
    citecolor=mediumgray
}

%% =============================================================================
%% METADATA
%% =============================================================================

\newcommand{\@reporttitle}{Research Radar}
\newcommand{\reporttitle}[1]{\renewcommand{\@reporttitle}{#1}}

\newcommand{\@reportstatus}{DRAFT}
\newcommand{\reportstatus}[1]{\renewcommand{\@reportstatus}{#1}}

\newcommand{\@issueinfo}{}
\newcommand{\issueinfo}[1]{\renewcommand{\@issueinfo}{#1}}

%% =============================================================================
%% HEADER / FOOTER
%% =============================================================================

\pagestyle{fancy}
\fancyhf{}
\fancyhead[L]{\small\textit{\@reporttitle}}
\fancyhead[R]{\small\textit{\@reportstatus}}
\fancyfoot[C]{\thepage}
\renewcommand{\headrulewidth}{0.3pt}
\renewcommand{\headrule}{\hbox to\headwidth{\color{rulegray}\leaders\hrule height \headrulewidth\hfill}}

\fancypagestyle{plain}{%
    \fancyhf{}%
    \fancyfoot[C]{\thepage}%
    \renewcommand{\headrulewidth}{0pt}%
}

\fancypagestyle{cover}{%
    \fancyhf{}%
    \renewcommand{\headrulewidth}{0pt}%
}

%% =============================================================================
%% TYPOGRAPHY
%% =============================================================================

\setlength{\parskip}{0.4em}
\renewcommand{\arraystretch}{1.2}
\arrayrulecolor{rulegray}

%% =============================================================================
%% SECTION STYLING
%% =============================================================================

\titleformat{\section}
    {\normalfont\Large\bfseries\color{accentblue}}
    {}
    {0pt}
    {}
    [\vspace{-0.5em}{\color{rulegray}\hrule height 0.6pt}\vspace{0.3em}]

\titleformat{\subsection}
    {\normalfont\large\bfseries\color{darkgray}}
    {}
    {0pt}
    {}

\titleformat{\subsubsection}
    {\normalfont\normalsize\bfseries\color{mediumgray}\itshape}
    {}
    {0pt}
    {}

\titlespacing*{\section}{0pt}{2ex plus 0.5ex}{1.2ex}
\titlespacing*{\subsection}{0pt}{1.8ex plus 0.3ex}{0.8ex}
\titlespacing*{\subsubsection}{0pt}{1.2ex plus 0.2ex}{0.5ex}

%% =============================================================================
%% PAPER CARD ENVIRONMENT
%% =============================================================================

\newcommand{\papercard}[1]{%
    \par\vspace{0.3em}%
    \noindent\fcolorbox{cardborder}{cardbg}{%
        \begin{minipage}{\dimexpr\linewidth-2\fboxsep-2\fboxrule}%
            \vspace{2pt}%
            #1%
            \vspace{2pt}%
        \end{minipage}%
    }%
    \par\vspace{0.3em}%
}

\newcommand{\insightbox}[1]{%
    \par\vspace{0.2em}%
    \noindent\colorbox{accentblue!4!white}{%
        \begin{minipage}{\dimexpr\linewidth-2\fboxsep}%
            \vspace{1pt}%
            {\small\itshape\color{accentblue!80!black}#1}%
            \vspace{1pt}%
        \end{minipage}%
    }%
    \par\vspace{0.2em}%
}

\newcommand{\metabox}[1]{%
    \par\vspace{0.2em}%
    \noindent\colorbox{lightgray}{%
        \begin{minipage}{\dimexpr\linewidth-2\fboxsep}%
            \vspace{1pt}%
            {\small #1}%
            \vspace{1pt}%
        \end{minipage}%
    }%
    \par\vspace{0.2em}%
}

%% =============================================================================
%% CUSTOM COMMANDS
%% =============================================================================

\newcommand{\papertitle}[1]{%
    {\Large\bfseries\color{darkgray}#1}%
    \par\vspace{2pt}%
}

\newcommand{\papermeta}[1]{%
    {\small\color{mediumgray}#1}%
    \par\vspace{4pt}%
}

\newcommand{\insight}[1]{%
    \begin{insightbox}
        {\small\itshape\color{accentblue!80!black}#1}
    \end{insightbox}%
}

\newcommand{\confidencebadge}[1]{%
    \ifnum\pdfstrcmp{#1}{HIGH}=0
        \colorbox{accentgreen!20}{\textcolor{accentgreen!80!black}{\footnotesize\bfseries\space HIGH\space}}%
    \fi
    \ifnum\pdfstrcmp{#1}{MEDIUM}=0
        \colorbox{accentorange!20}{\textcolor{accentorange!80!black}{\footnotesize\bfseries\space MED\space}}%
    \fi
    \ifnum\pdfstrcmp{#1}{LOW}=0
        \colorbox{accentred!20}{\textcolor{accentred!80!black}{\footnotesize\bfseries\space LOW\space}}%
    \fi
}

\newcommand{\areabox}[1]{%
    \colorbox{accentblue!15}{\textcolor{accentblue!80!black}{\footnotesize\bfseries\space #1\space}}%
}

\newcommand{\claimlist}[1]{%
    \begin{itemize}
        \renewcommand{\labelitemi}{\textcolor{accentblue}{\small$\bullet$}}
        #1
    \end{itemize}%
}

\newcommand{\compactlist}[1]{%
    \begin{itemize}
        \renewcommand{\labelitemi}{\textcolor{mediumgray}{\scriptsize$\triangleright$}}
        #1
    \end{itemize}%
}

%% =============================================================================
%% COLUMN TYPES
%% =============================================================================

\newcolumntype{L}[1]{>{\raggedright\arraybackslash}p{#1}}
\newcolumntype{C}[1]{>{\centering\arraybackslash}p{#1}}

%% =============================================================================
%% TOC STYLING
%% =============================================================================

\titlecontents{section}
    [0pt]
    {\addvspace{0.5em}\bfseries\color{accentblue}}
    {\thecontentslabel\hspace{0.5em}}
    {}
    {\hfill\color{mediumgray}\thecontentspage}

\titlecontents{subsection}
    [1.5em]
    {\color{darkgray}}
    {\thecontentslabel\hspace{0.5em}}
    {}
    {\titlerule*[0.5em]{\color{rulegray}.}\color{mediumgray}\thecontentspage}

\endinput
