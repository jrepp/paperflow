%% exec-report.cls
%% A clean, professional LaTeX document class for executive reports
%% Muted palette, 7"x9.25" format, pdflatex compatible
%%
%% Shared across multiple reports. Each report sets:
%%   \reporttitle{...}   — header center text
%%   \reportstatus{...}  — header left badge (e.g. DRAFT, FINAL)
%%
\NeedsTeXFormat{LaTeX2e}
\ProvidesClass{exec-report}[2026/02/20 Executive Report]

% Base on report class (10pt, single-sided, chapters supported)
\LoadClass[10pt,oneside]{report}

%% =============================================================================
%% PACKAGES
%% =============================================================================

\RequirePackage[utf8]{inputenc}
\RequirePackage[T1]{fontenc}
\RequirePackage{lmodern}
\RequirePackage[
    paperwidth=7in,
    paperheight=9.25in,
    margin=0.75in,
    headheight=13pt
]{geometry}

% Colors (table option enables \rowcolors)
\RequirePackage[table]{xcolor}
\RequirePackage{graphicx}
\RequirePackage{fancyhdr}
\RequirePackage{booktabs}
\RequirePackage{parskip}
\RequirePackage{array}
\RequirePackage{colortbl}
\RequirePackage{caption}

% Diagrams
\RequirePackage{tikz}
\usetikzlibrary{
    positioning,
    shapes.geometric,
    arrows.meta,
    calc,
    fit,
    backgrounds
}

% Code listings
\RequirePackage{listings}
\lstset{
    basicstyle=\ttfamily\small\color{darkgray},
    backgroundcolor=\color{lightgray},
    keywordstyle=\color{accentblue}\bfseries,
    commentstyle=\color{mediumgray}\itshape,
    stringstyle=\color{accentgreen},
    numbers=left,
    numberstyle=\tiny\color{mediumgray},
    numbersep=10pt,
    xleftmargin=20pt,
    xrightmargin=8pt,
    aboveskip=10pt,
    belowskip=10pt,
    frame=single,
    rulecolor=\color{bordergray},
    breaklines=true,
    tabsize=4,
    showstringspaces=false
}

% Go language definition for listings
\lstdefinelanguage{Go}{
    morekeywords=[1]{break,default,func,interface,select,case,defer,go,
        map,struct,chan,else,goto,package,switch,const,fallthrough,if,
        range,type,continue,for,import,return,var},
    morekeywords=[2]{bool,byte,complex64,complex128,error,float32,float64,
        int,int8,int16,int32,int64,rune,string,uint,uint8,uint16,uint32,
        uint64,uintptr,any,comparable},
    morekeywords=[3]{true,false,nil,iota,append,cap,close,complex,copy,
        delete,imag,len,make,new,panic,print,println,real,recover},
    sensitive=true,
    morecomment=[l]{//},
    morecomment=[s]{/*}{*/},
    morestring=[b]",
    morestring=[b]',
    morestring=[b]`,
}

% Chapter/section styling
\RequirePackage{titlesec}
\RequirePackage{titletoc}

% Hyperlinks (load last among formatting packages)
\RequirePackage{hyperref}
\hypersetup{
    colorlinks=true,
    linkcolor=darkgray,
    urlcolor=accentblue,
    citecolor=mediumgray
}

%% =============================================================================
%% COLOR DEFINITIONS — Muted Professional Palette
%% =============================================================================

\definecolor{darkgray}{RGB}{51,51,51}
\definecolor{mediumgray}{RGB}{102,102,102}
\definecolor{lightgray}{RGB}{245,245,245}
\definecolor{bordergray}{RGB}{180,180,180}
\definecolor{accentblue}{RGB}{41,98,150}
\definecolor{accentgreen}{RGB}{46,139,87}
\definecolor{accentorange}{RGB}{204,119,34}
\definecolor{accentred}{RGB}{180,60,60}
\definecolor{accentyellow}{RGB}{180,160,40}

%% =============================================================================
%% REPORT METADATA COMMANDS
%% =============================================================================

\newcommand{\@reporttitle}{Executive Report}
\newcommand{\reporttitle}[1]{\renewcommand{\@reporttitle}{#1}}

\newcommand{\@reportstatus}{DRAFT}
\newcommand{\reportstatus}[1]{\renewcommand{\@reportstatus}{#1}}

%% =============================================================================
%% CUSTOM BOX ENVIRONMENTS
%% =============================================================================

%% Executive summary — intended for the title page only.
%% Left accent bar, tight inner padding, compact paragraph spacing.
\newcommand{\execsummary}[1]{%
    \par\vspace{1em}%
    \noindent\hspace{-2pt}%
    \begin{tikzpicture}[baseline=(content.base west)]
        % Content node
        \node[
            inner xsep=12pt,
            inner ysep=10pt,
            text width=\dimexpr\linewidth-28pt\relax,
            anchor=base west,
            font=\small
        ] (content) {%
            \setlength{\parskip}{0.5em}%
            \noindent{\normalsize\sffamily\bfseries\color{accentblue}Executive Summary}%
            \par\noindent\color{bordergray}\rule{\dimexpr\linewidth-24pt}{0.4pt}%
            \par\noindent\color{darkgray}#1%
        };
        % Left accent bar (slightly wider for emphasis)
        \fill[accentblue]
            ([xshift=1pt]content.north west) rectangle
            ([xshift=4pt]content.south west);
        % Subtle background
        \fill[lightgray, opacity=0.3]
            ([xshift=4pt]content.north west) rectangle (content.south east);
    \end{tikzpicture}%
    \par\vspace{1em}%
}

\newcommand{\simplebox}[2]{%
    \par\vspace{0.4em}%
    \noindent\colorbox{lightgray}{%
        \begin{minipage}{\dimexpr\linewidth-2\fboxsep}%
            \smallskip\noindent\textbf{\small #1}\par\noindent{\small #2}\smallskip%
        \end{minipage}%
    }%
    \par\vspace{0.4em}%
}

\newcommand{\warnbox}[2]{%
    \par\vspace{0.4em}%
    \noindent\fcolorbox{bordergray}{white}{%
        \begin{minipage}{\dimexpr\linewidth-2\fboxsep-2\fboxrule}%
            \smallskip\noindent\textbf{\small #1}\par\noindent{\small #2}\smallskip%
        \end{minipage}%
    }%
    \par\vspace{0.4em}%
}

\newcommand{\goalbox}[2]{%
    \par\vspace{0.4em}%
    \noindent\fcolorbox{accentblue}{white}{%
        \begin{minipage}{\dimexpr\linewidth-2\fboxsep-2\fboxrule}%
            \smallskip\noindent\textcolor{accentblue}{\textbf{\small #1}}\par\noindent{\small #2}\smallskip%
        \end{minipage}%
    }%
    \par\vspace{0.4em}%
}

%% =============================================================================
%% HEADER / FOOTER
%% =============================================================================

\pagestyle{fancy}
\fancyhf{}
\fancyhead[L]{\colorbox{mediumgray}{\color{white}\small\bfseries\ \@reportstatus\ }}
\fancyhead[C]{\small\textit{\@reporttitle}}
\fancyhead[R]{\thepage}
\renewcommand{\headrulewidth}{0.4pt}

% Plain style for chapter opening pages
\fancypagestyle{plain}{%
    \fancyhf{}%
    \fancyfoot[C]{\thepage}%
    \renewcommand{\headrulewidth}{0pt}%
}

%% =============================================================================
%% SPACING
%% =============================================================================

\setlength{\parskip}{0.5em}
\renewcommand{\arraystretch}{1.3}
\arrayrulecolor{bordergray}

%% =============================================================================
%% CHAPTER / SECTION STYLING
%% =============================================================================

\titleformat{\chapter}[display]
    {\normalfont\huge\bfseries\color{darkgray}}
    {\color{accentblue}\chaptertitlename\ \thechapter}
    {10pt}
    {}

\titleformat{\section}
    {\normalfont\Large\bfseries\color{accentblue}}
    {\thesection}
    {1em}
    {}
    [\color{bordergray}\titlerule]

\titleformat{\subsection}
    {\normalfont\large\bfseries\color{darkgray}}
    {\thesubsection}
    {1em}
    {}

\titleformat{\subsubsection}
    {\normalfont\normalsize\bfseries\color{mediumgray}}
    {\thesubsubsection}
    {1em}
    {}

\titlespacing*{\chapter}{0pt}{-20pt}{20pt}
\titlespacing*{\section}{0pt}{3.5ex plus 1ex minus .2ex}{2.3ex plus .2ex}
\titlespacing*{\subsection}{0pt}{3.25ex plus 1ex minus .2ex}{1.5ex plus .2ex}

%% =============================================================================
%% TABLE OF CONTENTS
%% =============================================================================

\titlecontents{chapter}
    [0pt]
    {\addvspace{1em}\bfseries\color{accentblue}}
    {\thecontentslabel\hspace{1em}}
    {}
    {\hfill\color{mediumgray}\thecontentspage}

\titlecontents{section}
    [2em]
    {\color{darkgray}}
    {\thecontentslabel\hspace{1em}}
    {}
    {\titlerule*[0.5em]{\color{bordergray}.}\color{mediumgray}\thecontentspage}

\titlecontents{subsection}
    [4em]
    {\small\color{mediumgray}}
    {\thecontentslabel\hspace{1em}}
    {}
    {\titlerule*[0.5em]{.}\thecontentspage}

%% =============================================================================
%% TABLE COLUMN TYPES
%% =============================================================================

\newcolumntype{L}[1]{>{\raggedright\arraybackslash}p{#1}}
\newcolumntype{C}[1]{>{\centering\arraybackslash}p{#1}}
\newcolumntype{R}[1]{>{\raggedleft\arraybackslash}p{#1}}

%% =============================================================================
%% CUSTOM COMMANDS
%% =============================================================================

% Inline code reference
\newcommand{\code}[1]{\texttt{\color{accentblue}#1}}

%% =============================================================================
%% CAPTION STYLING
%% =============================================================================

\captionsetup{
    font={small},
    labelfont={bf,color=accentblue},
    textfont={color=darkgray}
}

\endinput
