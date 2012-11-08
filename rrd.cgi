#!/usr/bin/perl -w
#
# rrd.cgi: The script for generating graphs for rrdtool statistics.
#
# Author: Haroon Rafique <haroon.rafique@utoronto.ca>
#
# Closely modelled after the script by Jan "Yenya" Kasprzak <kas@fi.muni.cz>
# mrtg-rrd.cgi available at: http://www.fi.muni.cz/~kas/mrtg-rrd/
# I did not like its limitations and tight coupling with MRTG
#

use strict;

use POSIX qw(strftime);
use Time::Local;
use Text::ParseWords;
use Date::Manip;
use CGI;
use LWP::UserAgent;
use HTTP::Request::Common qw(GET);
use File::Basename;
use File::Path;
use Image::Size qw(imgsize);
use List::Util qw(first);

use RRDs;

use vars qw(@config_files @all_config_files %targets $config_time
    %directories $imagetype $percent_h);
use constant HTML_PREAMBLE => <<EOT;
<!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.0 Strict//EN"
    "http://www.w3.org/TR/xhtml1/DTD/xhtml1-strict.dtd">
<html xmlns="http://www.w3.org/1999/xhtml" lang="en" xml:lang="en">
EOT
use constant SCRIPT_VERSION => '<!-- $Id$ -->';

# EDIT THIS to reflect all your RRD config files
# Since this is in a BEGIN block, changes here require a restart in
# mod_perl to take effect
BEGIN { @config_files = qw(
    /etc/rrd/rrd.cfg
    /etc/rrd/rrd-mysql.cfg
    /etc/rrd/rrd-tomcat.cfg
    /etc/rrd/rrd-network.cfg
    /etc/rrd/rrd-weather.cfg
    /etc/rrd/rrd-sar.cfg
    /etc/rrd/rrd-home.cfg
); }

# This depends on what image format your libgd (and rrdtool) uses
$imagetype = 'png'; # or make this 'gif';

# strftime(3) compatability test
$percent_h = '%-H';
$percent_h = '%H' if (strftime('%-H', gmtime(0)) !~ /^\d+$/);

sub main ($)
{
    my ($q) = @_;

    try_read_config($q->url());

    my $mode = $q->param('mode');
    defined $mode && do {
        do_archive($q, $mode);
        return;
    };

    my $path = $q->path_info();
    $path =~ s/^\///;
    $path =~ s/\/$//;
    if (defined $directories{$path}) {
        if ($q->path_info() =~ /\/$/) {
            print_dir($path, $q);
        } else {
            print "Location: ", $q->url(-path_info=>1), "/\n\n";
        }
        return;
    }

    my ($dir, $stat, $ext) = ($q->path_info() =~
        /^(.*)\/([^\/]+)(\.html|-(preview|hour|day|week|month|year)\.($imagetype|src))$/);

    $dir && $dir =~ s/^\///;
    $dir .= '/' if $dir;

    print_error('Undefined statistic: ' . $q->path_info())
        unless defined $stat and defined $targets{$dir . $stat};

    print_error('Incorrect directory: ' . $q->path_info())
        unless defined $targets{$dir . $stat}{directory} ||
        $targets{$dir . $stat}{directory} eq $dir;

    my $tgt = $targets{$dir . $stat};

    common_args($dir . $stat, $tgt, $q);

    # We may be running under mod_perl or something. Do not destroy
    # the original settings of timezone.
    my $oldtz; 
    if (defined $tgt->{timezone}) {
        $oldtz = $ENV{TZ};
        $ENV{TZ} = $tgt->{timezone};
    }

    my $start = $q->param('start');
    my $end = $q->param('end');
    if( defined $start || defined $end ) {
        do_custom_image($tgt, $start, $end);
        return;
    }

    if ($ext eq '.html') {
        do_html($tgt, $q);
    } elsif ($ext eq '-preview.' . $imagetype) {
        do_image($tgt, 'preview', 0, 1);
    } elsif ($ext eq '-hour.' . $imagetype) {
        do_image($tgt, 'hour', 0, 1);
    } elsif ($ext eq '-day.' . $imagetype) {
        do_image($tgt, 'day', 0, 1);
    } elsif ($ext eq '-week.' . $imagetype) {
        do_image($tgt, 'week', 0, 1);
    } elsif ($ext eq '-month.' . $imagetype) {
        do_image($tgt, 'month', 0, 1);
    } elsif ($ext eq '-year.' . $imagetype) {
        do_image($tgt, 'year', 0, 1);
    } elsif ($ext eq '-preview.src') {
        do_image($tgt, 'preview', 1, 0);
    } elsif ($ext eq '-hour.src') {
        do_image($tgt, 'hour', 1, 0);
    } elsif ($ext eq '-day.src') {
        do_image($tgt, 'day', 1, 0);
    } elsif ($ext eq '-week.src') {
        do_image($tgt, 'week', 1, 0);
    } elsif ($ext eq '-month.src') {
        do_image($tgt, 'month', 1, 0);
    } elsif ($ext eq '-year.src') {
        do_image($tgt, 'year', 1, 0);
    } else {
        print_error('Unknown extension: ' . $ext);
    }
    $ENV{TZ} = $oldtz
        if defined $oldtz;
}

sub do_html($$)
{
    my ($tgt, $q) = @_;

    my( undef, $xh, $yh ) = do_image($tgt, 'hour',   0, 0)
        unless $tgt->{suppress} =~ /h/ or
        $tgt->{config}{interval} ne '1';
    my( undef, $xd, $yd ) = do_image($tgt, 'day',   0, 0);
    my( undef, $xw, $yw ) = do_image($tgt, 'week',  0, 0);
    my( undef, $xm, $ym ) = do_image($tgt, 'month', 0, 0);
    my( undef, $xy, $yy ) = do_image($tgt, 'year',  0, 0);

    http_headers('text/html', $tgt->{config});
    print <<EOT;
@{[HTML_PREAMBLE]}
<head>
<link type="text/css" rel="stylesheet" href="$tgt->{config}{resourcedir}/style.css"/>
<title>
EOT
    print $tgt->{title} if defined $tgt->{title};
    print <<EOT;
</title>
</head>
<body>
<table border="0">
     <tr align="left" valign="top">
         <td>
EOT

    my $mtime = undef;
    if( !$tgt->{ignoretimestamps} ) {
        $mtime = (stat $tgt->{rrd})[9];
        print STDERR
            'Could not get status info for ', $tgt->{rrd}, '. ',
            'Missing symbolic link or incorrect permissions!', "\n"
            unless defined $mtime;
        $mtime ||= 0;
    }
    my $is_set_no_auto_refresh =
        ($q->param('autorefresh') and $q->param('autorefresh') eq 'no')
            ?  1 : 0;
    my $is_set_no_preview =
        ($q->param('preview') and $q->param('preview') eq 'no')
            ?  1 : 0;
    my $modified_href;
    if( $is_set_no_auto_refresh or $is_set_no_preview ) {
        if( $is_set_no_auto_refresh and $is_set_no_preview ) {
            $modified_href = '?autorefresh=no&amp;preview=no';
        } elsif( $is_set_no_auto_refresh ) {
            $modified_href = '?autorefresh=no';
        } else {
            $modified_href = '?preview=no';
        }
    } else {
        $modified_href = '';
    }
    my $link_toggle_auto_refresh;
    if( $is_set_no_auto_refresh and $is_set_no_preview ) {
        # both autorefresh and preview say "no"
        $link_toggle_auto_refresh =
            '<a class="navlink" href="' .
            $q->url(-absolute=>1,-path=>1) .
            '?preview=no">&Theta; Enable Autorefresh</a>';
    } elsif( $is_set_no_auto_refresh and !$is_set_no_preview ) {
        # autorefresh says "no"
        $link_toggle_auto_refresh =
            '<a class="navlink" href="' .
            $q->url(-absolute=>1,-path=>1) .
            '">&Theta; Enable Autorefresh</a>';
    } elsif( !$is_set_no_auto_refresh and $is_set_no_preview ) {
        # preview says "no"
        $link_toggle_auto_refresh =
            '<a class="navlink" href="?autorefresh=no&amp;preview=no">&Phi; Disable Autorefresh</a>';
    } else {
        # none of them say "no"
        $link_toggle_auto_refresh =
            '<a class="navlink" href="?autorefresh=no">&Phi; Disable Autorefresh</a>';
    }
    print <<EOT;
<div id="menu">
<h1 class="firstheading">Navigation</h1>
<a class="navlink"
    href="./$modified_href">&uarr; Up to parent level (..)</a>
$link_toggle_auto_refresh
<div class="menuitem">
@{[ ($tgt->{suppress} =~ /h/ or $tgt->{config}{interval} ne '1') ? '' : '<a href="#Hourly">Hourly</a>|' ]}
@{[ $tgt->{suppress} =~ /d/ ? '' : '<a href="#Daily">Daily</a>|' ]}
@{[ $tgt->{suppress} =~ /w/ ? '' : '<a href="#Weekly">Weekly</a>|' ]}
@{[ $tgt->{suppress} =~ /m/ ? '' : '<a href="#Monthly">Monthly</a>|' ]}
@{[ $tgt->{suppress} =~ /y/ ? '' : '<a href="#Yearly">Yearly</a>|' ]}
<a href="#Historical">Historical</a>|
<a href="#Archived">Archived</a> Graphs
</div>
EOT

    print <<EOT if defined $tgt->{pagetop};
<h1 class="subheading">Title</h1>
<div class="menuitem">$tgt->{pagetop}</div>
EOT

    print <<EOT unless defined $tgt->{ignoretimestamps};
<h1 class="subheading">Timestamp</h1>
<div class="menuitem">
@{[ strftime("%A, %d %B, %H:%M:%S %Z", localtime($mtime)) ]}
EOT

    print <<EOT;
</div>
</div>
        </td>
        <td style="padding-top: 50px;">
EOT

                # total number of graphs (either 4 or 5)
    my $total_graphs = $tgt->{config}{interval} ne '1' ? 4 : 5;
                # How many are suppressed?
    my( $suppressed_graphs ) =
        $tgt->{config}{interval} ne '1' ?
            $tgt->{suppress} =~ /([dwmy]+)/ :
            $tgt->{suppress} =~ /([hdwmy]+)/;
    $suppressed_graphs ||= "";
    print '<div id="summary">';
    print '<h1>', $total_graphs-length($suppressed_graphs), ' Graphs(s)</h1>';
    $suppressed_graphs
        and print '<p>', length($suppressed_graphs), ' graph(s) suppressed</p>';
    print '</div>';

    my $dayavg = $tgt->{config}->{interval};

#    print '<!--';
#    use Data::Dumper;
#    print Dumper(%targets);
#    print '-->', "\n";

    html_graph($tgt, 'hour', 'Hourly', $dayavg . ' Minute', $xh, $yh);
    html_graph($tgt, 'day', 'Daily', '5 Minute', $xd, $yd);
    html_graph($tgt, 'week', 'Weekly', '30 Minute', $xw, $yw);
    html_graph($tgt, 'month', 'Monthly', '2 Hour', $xm, $ym);
    html_graph($tgt, 'year', 'Yearly', '1 Day', $xy, $yy);

    print <<EOT;
        </td>
    </tr>
</table>
<div>
<b><a name="Historical">Run-time Historical Graphs</a></b>
<small>These historical graphs produce images that are not cached at
all and hence carry a performance hit every time they are requested,
so be gentle</small>
EOT
    if( $tgt->{suppress} !~ /h/ and $tgt->{config}{interval} eq '1' ) {
    print '<br/>', "\n";
        foreach my $i (1..6) {
            print '<a href="?start=', -$i, 'h">',
                $i, ' hour', $i > 1 ? 's' : '', ' ago',
                '</a>', "\n";
        }
    }
    print '<br/>', "\n";
    foreach my $i (1..7) {
        print '<a href="?start=', -$i, 'd">',
            $i, ' day', $i > 1 ? 's' : '', ' ago',
            '</a>', "\n";
    }
    print '<br/>', "\n";
    foreach my $i (1..4) {
        print '<a href="?start=', -$i, 'w">',
            $i, ' week', $i > 1 ? 's' : '', ' ago',
            '</a>', "\n";
    }
    print '<br/>', "\n";
    foreach my $i (1..6) {
        print '<a href="?start=', -$i, 'm">',
            $i, ' month', $i > 1 ? 's' : '', ' ago',
            '</a>', "\n";
    }
    print <<EOT;
<form method="post" action="@{[ $q->url(-absolute=>1,-path=>1) ]}">
<div>
Arbitrary start and end dates:<br/>
Start Date: <input type="text" name="start" size="6" maxlength="40"/>
End Date: <input type="text" name="end" size="6" maxlength="40"/>
<input type="submit"/>
</div>
</form>
<div style="font-size: 80%"><dl>
<dt>Some examples of date specification for the above 2 inputs are:</dt>
<dd>today</dd>
<dd>1st thursday in June 1992</dd>
<dd>05/10/93</dd>
<dd>April 1, 2003</dd>
<dd>2 days ago</dd>
<dd>15 weeks ago</dd>
<dd>..., etc.</dd>
</dl>
</div>
EOT

    print <<EOT;
<div id="footer">
<b><a name="Archived">Archived Graphs</a></b>
<small>These are archived snapshots kept on the filesystem. Serving them
up via a web-viewable directory carries a very low performance hit.</small>
<br/>
Display of
<a href="?mode=daily">daily</a>,
<a href="?mode=monthly">monthly</a>,
<a href="?mode=yearly">yearly</a> archival modes is supported.
<br/>
EOT

    print <<EOT;
<a href="http://www.rrdtool.org/"><img
    src="$tgt->{config}{resourcedir}/rrdtool-logo-light.png" width="121"
    height="48" alt="RRDTool"/></a>
</div>
</div>
EOT

    print <<EOT;
@{[ SCRIPT_VERSION ]}
</body>
</html>
EOT

}

sub html_graph($$$$$$)
{
    my ($tgt, $ext, $freq, $period, $xsize, $ysize) = @_;

    return unless defined $tgt->{$ext};

    print <<EOT;
<br/><a name="$freq"><b>"$freq" Graph ($period Average)</b></a><br/>
<img src="$tgt->{url}-$ext.$imagetype"
width="$xsize" height="$ysize"
alt="$freq Graph"/><br/>
EOT

    print <<EOT;
<div style="font-size: 85%;"><a href="$tgt->{url}-$ext.src">[source]</a></div>
EOT
}

sub fp_equal {
    my ($X, $Y, $POINTS) = @_;
    my ($tX, $tY);
    $tX = sprintf("%.${POINTS}g", $X);
    $tY = sprintf("%.${POINTS}g", $Y);
    return $tX eq $tY;
}

sub http_headers($$)
{
    my ($content_type, $cfg) = @_;
    my $interval = $cfg->{interval};
    $interval ||= 5;
    my $refresh = $cfg->{refresh};
    $refresh ||= 300;

    print 'Content-Type: ', $content_type,
            ($content_type eq 'text/html' ? '; charset=iso-8859-1' : ''),
            "\n";

    if( %$cfg ) {
        # $cfg contains a reference to a non-empty hash

        # pragma header
        print 'Pragma: no-cache', "\n";

        # Don't print refresh headers for graphics and
        # when asked not to
        my $autorefresh = defined $cfg->{autorefresh}
            ? $cfg->{autorefresh} : '';
        print 'Refresh: ', $refresh, "\n"
            if $content_type ne "image/$imagetype" and
                $autorefresh ne 'no';

        # Expires header calculation stolen from CGI.pm
        print strftime("Expires: %a, %d %b %Y %H:%M:%S GMT\n",
            gmtime(time+60*$interval));
    }
    print "\n";
}

sub do_image($$$$)
{
    my ($target, $freq, $wantsrc, $wantimage) = @_;

    my $file = $target->{$freq};

    do {
        print_error("Target '$freq' suppressed for this target") if $wantimage;
        return;
    } unless defined $file;

    # Now the vertical rule at the end of the day
    my @t = localtime(time);
    # set seconds, minutes, hours to zero
    $t[0] = $t[1] = $t[2] = 0 unless $freq eq 'hour';

    my $seconds;
    my $oldsec;
    my $back;
    my $xgrid = '';

    if ($freq eq 'preview') {
        $seconds = timelocal(@t);
        $back = 10*3600;    # 10 hours
        $oldsec = $seconds - 1*864000;
    } elsif ($freq eq 'hour') {
        $seconds = timelocal(@t);
        $back = 3*3600;     # 3 hours
        $oldsec = $seconds - $t[2]*3600 - $t[1]*60 - $t[0];     # FIXME: where to set the VRULE
        $seconds = 0;
    } elsif ($freq eq 'day') {
        $seconds = timelocal(@t);
        $back = 30*3600;    # 30 hours
        $oldsec = $seconds - 86400;
        # We need this only for day graph. The other ones
        # are magically correct.
        $xgrid = 'HOUR:1:HOUR:6:HOUR:2:0:' . $percent_h;
    } elsif ($freq eq 'week') {
        $seconds = timelocal(@t);
        $t[6] = ($t[6]+6) % 7;
        $seconds -= $t[6]*86400;
        $back = 8*86400;    # 8 days
        $oldsec = $seconds - 7*86400;
    } elsif ($freq eq 'month') {
        $t[3] = 1;
        $seconds = timelocal(@t);
        $back = 36*86400;   # 36 days
        $oldsec = $seconds - 30*86400; # FIXME (the right # of days!!)
    } elsif ($freq eq 'year') {
        $t[3] = 1;
        $t[4] = 0;
        $seconds = timelocal(@t);
        $back = 396*86400;  # 365 + 31 days
        $oldsec = $seconds - 365*86400; # FIXME (the right # of days!!)
    } else {
        print_error("Unknown frequency: $freq");
    }

    my @local_args = ();

    if ($xgrid) {
        push @local_args, '-x', $xgrid;
    }

    my @graph_args = get_graph_args($target);

    my @common_graph_args = @{$target->{args}};

    if( $freq eq 'preview' ) {
        # find index of first array element which is equal to -W (watermark)
        my $watermark_index = first { $common_graph_args[$_] eq '-W' } 0..$#common_graph_args;

        # weed out -W (watermark) and it's argument
        if (defined $watermark_index) {
            splice(@common_graph_args, $watermark_index, 2);
        }

        # overwrite values for -h, -w, -W and introduce step size with -S
        push @graph_args,
                '-h', 80,
                '-w', 250,
                '-S', 300;
        # weed out legend related printing
        @graph_args = grep {!/^(GPRINT|COMMENT|PRINT)/i} @graph_args;
        # args with LINE1 or AREA should have multiple spaces stripped
        for( @graph_args ) {
            if( m/^(LINE1|AREA)/ ) {
                s/\s{2,}//g;
            }
        }
    }

    make_def_paths_absolute($target, \@graph_args);

    do {
        http_headers("text/html", $target->{config});
        print <<EOT;
@{[HTML_PREAMBLE]}
<head><title>Source</title></head>
<body>
EOT
        print '<pre>RRDs::graph(',
                join(",\n",
                $file, '-s', "-$back", @local_args,
                @common_graph_args, @graph_args, "VRULE:$oldsec#ff0000",
                "VRULE:$seconds#ff0000"),
                ')</pre></body></html>';
        return;
    } if $wantsrc;

    my $dir_name = dirname($file);
    if( !-d $dir_name ) {
        eval { mkpath $dir_name };
        if( $@ ) {
            print_error("Could not create $dir_name: $@");
        }
    }
   
    my( undef, $xsize, $ysize ) =
        RRDs::graph($file, '-s', "-$back", @local_args,
        @common_graph_args, @graph_args, "VRULE:$oldsec#ff0000",
        "VRULE:$seconds#ff0000");

    my $rrd_error = RRDs::error;
    print_error("RRDs::graph failed, $rrd_error") if defined $rrd_error;

    # on FreeBSD, RRDs::graph may return hugely wrong image size
    ( $xsize, $ysize ) = imgsize($file) if $xsize > 100000;

    # Do not proceed unless image is wanted
    return( undef, $xsize, $ysize ) unless $wantimage;

    # Return the exact image straight from the file
    open PNG, "<$file" or print_error("Can't open $file: $!");

    binmode PNG;

    http_headers("image/$imagetype", $target->{config});
        
    my $buf;
    # could be sendfile in Linux ;-)
    while(sysread PNG, $buf, 8192) {
        print $buf;
    }
    close PNG;
}

sub make_def_paths_absolute($$) {
    my $target = shift;     # target
    my $array_ref = shift;  # array reference to the graph arguments
    # make relative paths into absolute paths for DEFs
    for( @$array_ref ) {
        if( m/^DEF/i ) {
            # processing a line with DEF directive
            # check to see if rrd path is absolute
            my( $rrd_path ) = m#DEF:.*?=(/.*?):#g;
            if( !defined $rrd_path ) {
                # rrd path is relative
                # replace relative path with absolute by prepending
                # $target->{config}{logdir}/$target->{directory} to it
                s#
                    (DEF:.*?=)(.*?):
                #$1$target->{config}{logdir}/$target->{directory}/$2:#ix;
            }
        }
    }
}
 
sub get_graph_args($) {
    my $target = shift;
            # Use space as a delimeter to break up {graph} into a list
            # of words ignoring spaces inside quotes.
    my @graph_args = ();
    @graph_args =
            # eliminate all quotes and replace backslash-space with space
            map { s/"//og; s/\\ / /og; $_ }
            # The 2nd parameter is true which signifies that quotes,
            # backslashes, etc are kept in the return array
            quotewords('\s+', 1, $target->{graph})
                if defined $target->{graph};
    return @graph_args;
}

# prints a custom image for a historical/non-standard time interval
sub do_custom_image($$$) {
    my $target = shift;
    my $start = shift;
    my $end = shift;

    my( $start_time, $end_time ) = ( undef, undef );

    if( defined $start && defined $end ) {
        my( $start_date ) = ParseDate($start);
        my( $end_date )   = ParseDate($end);
        print_error("start date \"$start\" is not a parseable date")
            if $start_date eq '';
        print_error("end date \"$end\" is not a parseable date")
            if $end_date eq '';
        $start_time = UnixDate($start_date, "%s");
        $end_time = UnixDate($end_date, "%s");
        print_error("start \"$start\" should be less than end \"$end\"")
            if $start_time >= $end_time;
                # have to fix the x-axis for day interval
        push @{$target->{args}}, '-x', 'HOUR:1:HOUR:6:HOUR:2:0:' . $percent_h
            if ($end_time-$start_time) <= 86400;
    } elsif( defined $start ) {
        my( $interval, $type ) = ($start =~ m/(\-\d+)([hdwm])/);
                # regular -1d, -1m, -2w style start interval with no end
        if( defined $interval && defined $type ) {
                # work around a bug in time parsing code within rrdtool
                # interprets -6m as -6 minutes instead of -6 months
            $type = 'mon' if $type eq 'm';
                # start time is just interval-1
            $start_time = $interval-1 . $type;
                # for hourly interval type just go back three hours
            $start_time = $interval-3 . 'h' if $type eq 'h';
                # end time is equal to interval
            $end_time = $interval . $type;
                # have to fix the x-axis for day interval
            push @{$target->{args}}, '-x', 'HOUR:1:HOUR:6:HOUR:2:0:' . $percent_h
                if $type eq 'd';
        }
    }

    do {
        print_error('Undefined start or end time');
        return;
    } unless defined $start_time && defined $end_time;

    my @graph_args = get_graph_args($target);

    make_def_paths_absolute($target, \@graph_args);

    my( $fh, $filename );
    if( $ENV{MOD_PERL} ) {
        use File::Temp qw/ tempfile /;
        ( $fh, $filename )= tempfile( );
    } else {
            # unbuffered output
        $| = 1;
        $filename = '-';
    }
    http_headers("image/$imagetype", $target->{config});
    RRDs::graph($filename,
            '-s', $start_time,
            '-e', $end_time,
            @{$target->{args}}, @graph_args);
    if( $ENV{MOD_PERL} ) {
        binmode $fh;
        my $buf;
        while(sysread $fh, $buf, 8192) {
            print $buf;
        }
        close $fh;
        unlink $filename;
    }
    my $rrd_error = RRDs::error;
    print_error("RRDs::graph failed, $rrd_error") if defined $rrd_error;
}

sub common_args($$$)
{
    my ($name, $target, $q) = @_;

    my $cfg = $target->{config};

    my $autorefresh = $q->param('autorefresh') || '';
    if( $autorefresh eq 'no' ) {
        $cfg->{autorefresh} = 'no';
    } else {
        delete $cfg->{autorefresh};
    }

    return @{$target->{args}} if defined @{$target->{args}};

    $target->{name} = $name;

    $target->{directory} = ''
        unless defined $target->{directory};

    $target->{url} = $q->url . '/' . $name;

    my $dir = $cfg->{workdir};
    $dir = $cfg->{logdir}
        if defined $cfg->{logdir};

    $target->{rrd} = $dir . '/' . $name . '.rrd';

    $dir = $cfg->{workdir};
    $dir = $cfg->{imagedir}
        if defined $cfg->{imagedir};

    $target->{suppress} ||= '';

    $target->{preview}   = $dir . '/' . $name
        . '-preview.' . $imagetype unless $target->{suppress} =~ /p/;
    $target->{hour}   = $dir . '/' . $name
        . '-hour.' . $imagetype unless
        $target->{suppress} =~ /h/ or $cfg->{interval} ne '1';
    $target->{day}   = $dir . '/' . $name
        . '-day.' . $imagetype unless $target->{suppress} =~ /d/;
    $target->{week}  = $dir . '/' . $name
        . '-week.' . $imagetype unless $target->{suppress} =~ /w/;
    $target->{month} = $dir . '/' . $name
        . '-month.' . $imagetype unless $target->{suppress} =~ /m/;
    $target->{year}  = $dir . '/' . $name
        . '-year.' . $imagetype unless $target->{suppress} =~ /y/;

    if( $target->{config}{interval} eq '1' and $target->{suppress} !~ /h/ ) {
                # change the refresh interval only if hourly is enabled
        $target->{config}{refresh} = 60;
    } elsif( $target->{config}{interval} ne '5' ) {
                # custom interval
        $target->{config}{refresh} = 60 * $target->{config}{interval};
    }

    my @args = ();

    my $year = strftime "%Y", localtime;

    push @args, '--lazy',
        '-a', uc $imagetype,
        '-h', '120',
        '-w', '500',
        '-W', '© Haroon Rafique 2003-' . $year . '. All rights reserved. Unauthorised use prohibited.';

    @{$target->{args}} = @args;

    @args;
}

# store/display images from/to archive
sub do_archive($$)
{
    my $q = shift;
    my $mode = shift;

    do {
        print_error(<<EOT);
<h3>Invalid mode '$mode'</h3>
Only
<a href="?mode=archive">archive</a>,
<a href="?mode=daily">daily</a>,
<a href="?mode=monthly">monthly</a>,
<a href="?mode=yearly">yearly</a> modes are supported.
EOT
    } if $mode !~ m/^(archive|daily|monthly|yearly)$/o;

    # check to see if archive mode being requested via the web
    if( $mode eq 'archive' and $ENV{GATEWAY_INTERFACE} ) {
        print_error(<<EOT);
<h2>Should be used offline only</h2>
Invoke from command line as:
<pre>rrd.cgi mode=archive</pre>
EOT
    } elsif( $mode eq 'archive' ) {
        archive_directory(undef, undef);
        return;
    }
    my $date;
    my( $m, $d, $y );
    if( $q->param('date') ) {
        $date = $q->param('date');
        ( $m, $d, $y ) = split /-/, $date;
        unless( defined $m and defined $d and defined $y ) {
            # initialize missing date parameters
            if( $mode eq 'monthly' ) {
                $y = $d;
                # plug in 01 as the day
                $d = '01';
            }
            if( $mode eq 'yearly' ) {
                $y = $m;
                # plug in 01 as the day, 01 as the month
                $d = $m = '01';
            }
        }
        unless( defined $m and defined $d and defined $y
                and $m =~ /\d{2}/
                and $d =~ /\d{2}/
                and $y =~ /\d{4}/ ) {
            print_error(<<EOT)
<h3>Invalid date >>>$date<<<<</h3>
<b>Date parameter must be in mm-dd-yyyy format</b>
EOT
        }
    } else {
        # no date provided
        if( $mode eq 'daily' ) {
            # default to yesterday
            ( $m, $d, $y ) = UnixDate('yesterday', '%m', '%d', '%Y');
        } elsif( $mode eq 'monthly' ) {
            # default to 1 month ago
            ( $m, $d, $y ) = UnixDate('1 month ago', '%m', '%d', '%Y');
        } elsif( $mode eq 'yearly' ) {
            # default to 1 year ago
            ( $m, $d, $y ) = UnixDate('1 year ago', '%m', '%d', '%Y');
        }
    }
    my $parse_date = ParseDate($m.'/'.$d.'/'.$y);
    my $parse_time = UnixDate($parse_date, "%s");

    unless( defined $parse_time and
            $parse_time < UnixDate(ParseDate('today 12:00am'), "%s") ) {
        print_error(<<EOT)
<h3>We're sorry. Archived snapshots for $m-$d-$y are not available</h3>
We only carry Archived snapshots uptil yesterday.
EOT
    }
    display_archived_images($q, $m, $d, $y);
}

sub display_archived_images($$$$) {
    my $q = shift;
    my $m = shift;
    my $d = shift;
    my $y = shift;

    my $mode = $q->param('mode');

    my ($dir, undef, $stat, $ext) = ($q->path_info() =~
            m#^(.*)/(([^/]+)(\.html))?$#);

    if( !defined $dir ) {
        print_error('Undefined statistic ', $q->path_info(),
                ' for archive mode: ', $mode);
    }
    # now that $dir is verified immediately strip the leading slash
    $dir =~ s/^\///;

    unless( defined $directories{$dir}{config}{archiveurl} ) {
        print_error('Missing Archiveurl for ', $dir,
                ' for archive mode: ', $mode);
    }

    my $archive_url = $directories{$dir}{config}{archiveurl};

    my @targets = ();
    my $title;

    # if only $dir is defined it means user is requesting archived
    # images for the whole directory. Otherwise, if all of $dir, $stat
    # and $ext are defined, then the user is requesting a single
    # archived image
    if( !defined $stat or !defined $ext ) {
        # multiple archived images
        for my $target ( @{$directories{$dir}{target}} ) {
            push @targets, $target;
        }
        $title = 'Images for ' . $dir;
    } else {
        # single archived image
        push @targets, $dir . '/' . $stat;
        $title = 'Image for ' . $dir . '/' . $stat;
    }

    for( $mode ) {
        /daily/     && do { $title .= " daily mode for $m-$d-$y"; last; };
        /monthly/   && do { $title .= " monthly mode for $m-$y"; last; };
        /yearly/    && do { $title .= " yearly mode for $y"; last; };
    }

    my $resource_dir = $directories{$dir}{config}{resourcedir};
    $resource_dir = find_resource_dir($dir) unless defined $resource_dir;
    http_headers('text/html', undef);
    print <<EOT;
@{[HTML_PREAMBLE]}
<head>
<link type="text/css" rel="stylesheet" href="$resource_dir/style.css"/>
<title>RRD: Archived $title</title>
<script type="text/javascript" src="$resource_dir/CalendarPopup.js">
</script>
</head><body>
<div>
EOT

    generate_calendar($q, $mode, $m, $d, $y, $resource_dir);

    print 'Switch mode to:';
    for my $m ('daily', 'monthly', 'yearly') {
        print $mode eq $m
            ?
            ' ' . $m
            :
            ' [<a href="?mode=' . $m . '">' . $m . '</a>]';
    }
    print '<br/>';


    for my $target ( @targets ) {
        if(
                exists $targets{$target}{suppress} and
                ($targets{$target}{suppress} =~ /d/ and $mode eq 'daily'
                or
                $targets{$target}{suppress} =~ /m/ and $mode eq 'monthly'
                or
                $targets{$target}{suppress} =~ /y/ and $mode eq 'yearly')
                ) {
            # target is suppressed for this mode
            print '<b>', $targets{$target}{title},
                    '</b> <br/> <span style="margin-left: 20px;">',
                    '<b>', $mode, '</b> archive mode is suppressed',
                    ' (try another mode above)</span><br/>';
            next;
        }

        my $image_file;
        my $image_dir = $directories{$dir}{config}{archivedir} . '/' . $dir;
        for( $mode ) {
            /daily/     && do { $image_file = "$y/$m/$target-$y-$m-$d"; last; };
            /monthly/   && do { $image_file = "$y/$target-$y-$m"; last; };
            /yearly/    && do { $image_file = "$target-$y"; last; };
            print_error('Undefined mode, ', $mode);
        }
        # strip directory name from the file
        $image_file =~ s/$dir\/?//;
        $image_file .= '.' . $imagetype;

        unless( -f "$image_dir/$image_file" ) {
            my $current_month_year = strftime "%m-%Y", localtime;
            my( $cur_m, $cur_y ) = split /-/, $current_month_year;
            my $error_date = $mode eq 'daily' ?
                "$m-$d-$y" : $mode eq 'monthly' ?
                "$m-$y" : $y;
            # archived image does not exist for this mode
            # perhaps archival of images was started after that date
            print '<b>', $targets{$target}{title},
                    '</b> does not have a <b>', $mode,
                    '</b> archived image for <b>',
                    $error_date, '</b>.<br/>';
            if( $mode eq 'monthly' and $cur_y <= $y and $cur_m <= $m ) {
                my $avail_month = sprintf("%02d", $m+1); 
                my $avail_year = $y;
                # be careful when incrementing months beyond 12
                if( $m eq '12' ) {
                    $avail_month = '01';
                    $avail_year = $y+1;
                }
                print 'It will become available on <b>',
                      $avail_month, '-01-', $avail_year,
                      '</b>.<br/>', "\n";
            }
            if( $mode eq 'yearly' and $cur_y <= $y ) {
                print 'It will become available on <b>',
                      '01-01-', $y+1,
                      '</b>.<br/>', "\n";
            }
            next;
        }
        print <<EOT;
<b>$targets{$target}{title}</b>
<br/>
<img src="$archive_url/$dir/$image_file"/>
<br/>
EOT
    }
    print <<EOT;
</div>
@{[ SCRIPT_VERSION ]}
</body>
</html>
EOT
}

# generate code for JavaScript calendar
#   remember that, in JavaScript, the 2nd argument to
#   Date($y,@{[$m-1]},$d) needs to have 1 subtracted from it as the
#   JavaScript months go from 0 to 11
sub generate_calendar($$$$$$) {
    my $q = shift;
    my $mode = shift;
    my $m = shift;
    my $d = shift;
    my $y = shift;
    my $resource_dir = shift;

    print <<EOT;
<script type="text/javascript">
<!-- hide
var cal = new CalendarPopup('calDiv');

EOT

    print <<EOT if $mode eq 'daily';
        // get today's date
var today = new Date();
        // disabled dates later than today
cal.addDisabledDates(formatDate(today,'MM-dd-yyyy'),null);
cal.setReturnFunction('set_href');
EOT

    print <<EOT if $mode eq 'monthly';
cal.setDisplayType("month")
cal.setReturnMonthFunction('set_href');
cal.showYearNavigation();
EOT
    
    print <<EOT if $mode eq 'yearly';
cal.setDisplayType("year");
cal.setReturnYearFunction('set_href');
cal.showYearNavigation();
EOT
    
    print <<EOT;

try {
    cal.currentDate = new Date($y,@{[$m-1]},$d);
} catch( err ) {
    /* ignore */
}

/* since calendar is now in a div, lets print the necessary css */
document.write(cal.getStyles());

/* function to get input back from calendar popup
 * sanitizes the output by adding leading zeros LZ and sets the
 * location.href property
 */
function set_href(y, m, d) {
EOT

    print <<EOT if $mode eq 'daily';
    location.href = '?date='+LZ(m)+'-'+LZ(d)+'-'+y+'&mode=daily';
EOT

    print <<EOT if $mode eq 'monthly';
    location.href = '?date='+LZ(m)+'-01-'+y+'&mode=monthly';
EOT

    print <<EOT if $mode eq 'yearly';
    location.href = '?date='+'01-01-'+y+'&mode=yearly';
EOT

    print <<EOT;
}
// end hidding -->
</script>

<form method="post"
    action="@{[ $q->url(-absolute=>1,-path=>1) ]}">
    <div>
    <input type="hidden" name="mode" value="$mode"/>
    <input style="margin-left: 75px;" type="text" name="date"
EOT

    print <<EOT if $mode eq 'daily';
        value="$m-$d-$y" size="10"/>
EOT
    print <<EOT if $mode eq 'monthly';
        value="$m-$y" size="7"/>
EOT
    print <<EOT if $mode eq 'yearly';
        value="$y" size="4"/>
EOT

    my( $prev, $next, $parse_date );
    if( $mode eq 'daily' ) {
        $parse_date = ParseDate($m.'/'.$d.'/'.$y);
        $prev = UnixDate(DateCalc($parse_date, '-1 day'), '%m-%d-%Y');
        $prev = 'date=' . $prev . '&amp;mode=daily';
        $next = UnixDate(DateCalc($parse_date, '+1 day'), '%m-%d-%Y');
        $next = 'date=' . $next . '&amp;mode=daily';
    } elsif( $mode eq 'monthly' ) {
        $parse_date = ParseDate($m.'/01/'.$y);
        $prev = UnixDate(DateCalc($parse_date, '-1 month'), '%m-%d-%Y');
        $prev = 'date=' . $prev . '&amp;mode=monthly';
        $next = UnixDate(DateCalc($parse_date, '+1 month'), '%m-%d-%Y');
        $next = 'date=' . $next . '&amp;mode=monthly';
    } elsif( $mode eq 'yearly') {
        $parse_date = ParseDate('01/01/'.$y);
        $prev = UnixDate(DateCalc($parse_date, '-1 year'), '%m-%d-%Y');
        $prev = 'date=' . $prev . '&amp;mode=yearly';
        $next = UnixDate(DateCalc($parse_date, '+1 year'), '%m-%d-%Y');
        $next = 'date=' . $next . '&amp;mode=yearly';
    }

    print <<EOT;
    <a href="#"
        onclick="cal.showCalendar(this.id); return false;"
        name="calAnchor" id="calAnchor"><img
        width="34" height="21" alt="[calendar]"
        src="$resource_dir/calendar.gif"/></a>
    <span style="margin-left: 20px;">
        <a href="?$prev">&laquo;prev</a> <a href="?$next">next&raquo;</a>
    </span>
    </div>
</form>
<div id="calDiv"
    style="position:absolute; visibility:hidden; background-color:white;"></div>
EOT
}

sub try_read_config($)
{
    my ($prefix) = (@_);
    $prefix =~ s/\/[^\/]*$//;

    # Verify the version of RRDtool:
    if (!defined $RRDs::VERSION || $RRDs::VERSION < 1.2013) {
        print_error("Please install more up-to date RRDtool - need at least 1.2013");
    }
    
    my $read_cfg;
    if (!defined $config_time) {
            # must read config files initially
        $read_cfg = 1;
    } else {
        for my $file (@all_config_files) {
            my $mtime = (stat $file)[9];
            if ($config_time < $mtime) {
                    # modification time is newer than last read time
                $read_cfg = 1;
                last;
            }
        }
    }

    return unless $read_cfg;

    %targets = ();

    @all_config_files = @config_files;

    my $order = 0;
    for my $cfgfile (@all_config_files) {
        my $cfgref = {
            refresh => 300,
            interval => 5,
            resourcedir => $prefix
        };

        read_rrd_config($cfgfile, $cfgref, \$order);
    }

    delete $targets{_};

    parse_directories();

    $config_time = time;
}

sub read_rrd_config($$$)
{
    my ($file, $cfgref, $order) = @_;

    my @lines;

    open(CFG, "<$file") || print_error("Cannot open config file $file: $!");
    while (<CFG>) {
        chomp;                    # remove newline
        s/\s+$//;                 # remove trailing space
        s/\s+/ /g;                # collapse white spaces to one space
        next if /^ *\#/;          # skip comment lines
        next if /^\s*$/;          # skip empty lines
        if (scalar @lines and /^\s+\S/) {
                                # lines beginning with whitespace followed
                                # by content are really a continuation, so
                                # combine them
            $lines[$#lines] .= $_;
        } else {
            push @lines, $_;
        }
    }
    close CFG;

    my $dir = '';
    foreach (@lines) {
        if (/^\s*([\w\d]+)\[(\S+)\]\s*:\s*(.*)$/) {
            # reading a target line with square brackets
            my ($opt, $tgt, $val) = (lc($1), lc($2), $3);
            if( $opt eq 'directory' ) {
                if( exists $targets{$tgt} ) {
                    print_error("Parse error in <pre>$file</pre> ",
                        "near <pre>$_</pre> ",
                        "A Directory[] directive must appear before any ",
                        "other directives for a target.");
                }
                $dir = $val . '/';
            }
            unless( exists $targets{$dir . $tgt} ) {
                if( $opt ne 'directory' ) {
                    $dir = '';
                }
                $targets{$dir . $tgt}{name} = $dir . $tgt;
                $targets{$dir . $tgt}{directory} = $dir;
                $targets{$dir . $tgt}{order} = ++$$order;
                $targets{$dir . $tgt}{config} = $cfgref;
            }
            if( exists $targets{$dir . $tgt}{$opt} ) {
                # duplicate found, so inform user
                if( $opt ne 'directory' ) {
                    if( exists $targets{$tgt}{$opt} ) {
                        print_error("Parse error in <pre>$file</pre> ",
                            "near <pre>$_</pre> ",
                            "Duplicate target entry found (<b>$tgt</b> ",
                            "exists already as a target). Either change ",
                            "the target name or provide a ",
                            "<pre>Directory[$tgt]: some_new_dir</pre> ",
                            "directive before specifying this line.");
                    } else {
                        $dir = '';
                        $targets{$dir . $tgt}{name} = $dir . $tgt;
                        $targets{$dir . $tgt}{directory} = $dir;
                        $targets{$dir . $tgt}{order} = ++$$order;
                        $targets{$dir . $tgt}{config} = $cfgref;
                    }
                }
            }
            $targets{$dir . $tgt}{$opt} = $val;
            next;
        } elsif (/^([\w\d]+)\s*:\s*(\S.*)$/) {
            # reading a configuration line (e.g., Imagedir, Logdir, etc)
            my ($opt, $val) = (lc($1), $2);
            $cfgref->{$opt} = $val;
            next;
        }
        print_error("Parse error in $file near $_");
    }
}

sub parse_directories {
    %directories = ();

    # sorted names using the Schwartzian Transform (read comments backwards)
    my @names =
        map { $_->[0] }                         # restore original values
        sort { $a->[1] <=> $b->[1] }            # sort
        map { [ $_, $targets{$_}{order} ] }     # transform: value, sortkey
        keys %targets;

    my %is_in_subdir_list = ();
    for my $name (@names) {
        my $dir = $targets{$name}{directory}
            if defined $targets{$name}{directory};
        $dir = '' unless defined $dir;

        my $prefix = '';
        for my $component (split /\/+/, $dir) {
            unless (defined $directories{$prefix.$component}
                    or $is_in_subdir_list{$prefix.$component}) {
                push (@{$directories{$prefix}{subdir}},
                    $component);
                $is_in_subdir_list{$prefix.$component} = 1;
            }
            if( $prefix eq '' ) {
                # with an empty prefix, use the component itself as the
                # next prefix
                $prefix = $component;
            } else {
                $prefix .= '/' . $component;
            }
        }
        unless (defined $directories{$dir}) {
            $directories{$dir}{config} = $targets{$name}{config};
        }
        push (@{$directories{$dir}{target}}, $name);
    }
}

sub find_resource_dir($);

sub find_resource_dir($) {
    # find resource directory by descending into subdirectories
    # recursively until found
    my $dir = shift;
    my $resource_dir;
    my $subdirs = $directories{$dir}{subdir};
    my $first_subdir;
    if( defined $subdirs ) {
        $first_subdir = @{$subdirs}[0];
        $first_subdir = $dir . '/' . $first_subdir unless $dir eq '';
        $resource_dir =
            $directories{$first_subdir}{config}{resourcedir};
        # recurse deeper into next directory level
        $resource_dir = find_resource_dir($first_subdir)
            unless defined $resource_dir;
    }
    return $resource_dir;
}

sub print_dir($$) {
    my ($dir, $q) = @_;

    my $dir1 = $dir . '/';

    my( $summary ) = {graphs => 0, suppress => 0, subdir => 0};
    # run over all the targets in this directory for summary stats
    if (defined @{$directories{$dir}{target}}) {
        for my $item (@{$directories{$dir}{target}}) {
            $summary->{graphs}++;
            # see if item is suppressed?
            if( defined $targets{$item}{suppress} ) {
                if( ($targets{$item}{suppress} =~ /d/ &&
                            $targets{$item}{config}{interval} ne '1') ||
                        ($targets{$item}{suppress} =~ /h/ &&
                         $targets{$item}{config}{interval} eq '1') ) {
                    $summary->{suppress}++;
                }
            }
        }
    }

    # run over all the targets in this directory to see if any of them
    # has interval as 1 meaning a refresh of 60
    if (defined @{$directories{$dir}{target}}) {
        for my $item (@{$directories{$dir}{target}}) {
            common_args($item, $targets{$item}, $q);
            if( $targets{$item}{config}{interval} eq '1'
                    && $targets{$item}{suppress} !~ /h/ ) {
                $directories{$dir}{config}{refresh} = 60;
                last;
            }
        }
    }
    http_headers('text/html', $directories{$dir}{config});

    my $resource_dir = $directories{$dir}{config}{resourcedir};
    $resource_dir = find_resource_dir($dir) unless defined $resource_dir;
    my $is_set_no_auto_refresh =
        ($q->param('autorefresh') and $q->param('autorefresh') eq 'no')
            ?  1 : 0;
    my $is_set_no_preview =
        ($q->param('preview') and $q->param('preview') eq 'no')
            ?  1 : 0;
    my $modified_href;
    if( $is_set_no_auto_refresh or $is_set_no_preview ) {
        if( $is_set_no_auto_refresh and $is_set_no_preview ) {
            $modified_href = '?autorefresh=no&amp;preview=no';
        } elsif( $is_set_no_auto_refresh ) {
            $modified_href = '?autorefresh=no';
        } else {
            $modified_href = '?preview=no';
        }
    } else {
        $modified_href = '';
    }

    print <<EOT;
@{[HTML_PREAMBLE]}
<head>
<link type="text/css" rel="stylesheet" href="$resource_dir/style.css"/>
<title>RRD: Directory $dir1</title>
EOT

    print <<EOT unless $is_set_no_preview and @{$directories{$dir}{target}};
<style type="text/css">
#graphs { padding-top: 0; clear: left; margin-left: 5px; }
#nav { width: 30%; }
</style>
EOT
    print <<EOT if @{$directories{$dir}{target}};
<script type="text/javascript" src="$resource_dir/overlibmws.js">
</script>
<script type="text/javascript">
<!-- hide
function OLpreviewImage(src) {
    return '<img src="' + src + '"/>';
}
// end hiding -->
</script>
EOT
    print <<EOT;
</head>
EOT

    my( @graphs, @graph_text, @nav_text, @subdir_text );

    if (defined @{$directories{$dir}{subdir}}) {
        push @subdir_text, <<EOT;
            <h1 class="subheading">Subdirectories in $dir1</h1>
            <div class="menuitem">
            <small>More graphs are available in the following subdirectories</small>
            <ul class="listAsTable">
EOT
        for my $item (@{$directories{$dir}{subdir}}) {
            push @subdir_text, <<EOT;
            <li>&raquo; <a href="$item/$modified_href">$item/</a></li>
EOT
            $summary->{subdir}++;
        }

        push @subdir_text, <<EOT;
            </ul>
            </div>
EOT
    }

    print <<EOT;
<body>
<div id="container">
EOT

    if ( $dir ne '' ) {
        my $link_toggle_auto_refresh;
        my $link_toggle_preview;
        if( $is_set_no_auto_refresh and $is_set_no_preview ) {
            # both autorefresh and preview say "no"
            $link_toggle_auto_refresh =
                '<a class="navlink" href="' .
                $q->url(-absolute=>1,-path=>1) .
                '?preview=no">&Theta; Enable Autorefresh</a>';
            $link_toggle_preview =
                '<a class="navlink" href="' .
                $q->url(-absolute=>1,-path=>1) .
                '?autorefresh=no">&Theta; Enable Preview</a>';
        } elsif( $is_set_no_auto_refresh and !$is_set_no_preview ) {
            # autorefresh says "no"
            $link_toggle_auto_refresh =
                '<a class="navlink" href="' .
                $q->url(-absolute=>1,-path=>1) .
                '">&Theta; Enable Autorefresh</a>';
            $link_toggle_preview =
                '<a class="navlink" href="?autorefresh=no&amp;preview=no">&Phi; Disable Preview</a>';
        } elsif( !$is_set_no_auto_refresh and $is_set_no_preview ) {
            # preview says "no"
            $link_toggle_preview =
                '<a class="navlink" href="' .
                $q->url(-absolute=>1,-path=>1) .
                '">&Theta; Enable Preview</a>';
            $link_toggle_auto_refresh =
                '<a class="navlink" href="?autorefresh=no&amp;preview=no">&Phi; Disable Autorefresh</a>';
        } else {
            # none of them say "no"
            $link_toggle_auto_refresh =
                '<a class="navlink" href="?autorefresh=no">&Phi; Disable Autorefresh</a>';
            $link_toggle_preview =
                '<a class="navlink" href="?preview=no">&Phi; Disable Preview</a>';
        }

        push @nav_text, <<EOT;
            <a class="navlink"
                href="../$modified_href">&uarr; Up to parent level (..)</a>
EOT
        push @nav_text, <<EOT if defined @{$directories{$dir}{target}};
            $link_toggle_auto_refresh
            $link_toggle_preview
EOT
    }

    push @nav_text, <<EOT if defined @{$directories{$dir}{subdir}};
@subdir_text
EOT

    if (defined @{$directories{$dir}{target}}) {
        push @nav_text, <<EOT;
            <h1 class="subheading">Title</h1>
            <div class="menuitem">RRD graphs in:
                <div id="directory">$dir1</div>
            </div>
EOT
        push @nav_text, <<EOT if $is_set_no_preview;
            <h1 class="subheading">Available Graphs</h1>
            <div class="menuitem">
EOT
        push @graph_text, <<EOT;
<small>Click on a graphic to go to a deeper level.</small>
EOT
        push @graph_text, <<EOT unless $is_set_no_preview;
<small>Mouseover for individual zoom. <strong>preview</strong> enabled</small>
EOT
        push @graph_text, <<EOT unless $is_set_no_auto_refresh;
<small><strong>autorefresh</strong> enabled</small>
EOT
        push @graph_text, <<EOT unless $is_set_no_preview and $is_set_no_auto_refresh;
<small>(disable using navigation)</small>.
EOT
        push @graph_text, <<EOT;
<br/>
EOT

        for my $item (@{$directories{$dir}{target}}) {
            my $itemname = $item;
            common_args($item, $targets{$item}, $q);
            my( $freq, $freqtext );
            if( !$is_set_no_preview ) {
                $freq = 'preview';
                $freqtext = 'Preview';
            } elsif( $targets{$item}{config}{interval} eq '1' ) {
                $freq = 'hour';
                $freqtext = 'Hourly';
            } else {
                $freq = 'day';
                $freqtext = 'Daily';
            }
            my( undef, $xsize, $ysize ) =
                do_image($targets{$item}, $freq, 0, 0);
            $itemname = $targets{$item}{title}
                if defined $targets{$item}{title};
                    # for each graph store its item and name in an
                    # anonymous hash and push onto the array @graphs
            my $item_relative = $item;
            # strip any directories from $item (first occurrence)
            $item_relative =~ s/$targets{$item}{directory}\/?//;
            push @graphs, {item => $item_relative, name => $itemname};
            if(     (exists $targets{$item}{suppress} &&
                    $targets{$item}{suppress} =~ /p/) ||
                    (exists $targets{$item}{suppress} &&
                    $targets{$item}{suppress} =~ /d/ &&
                    $is_set_no_preview &&
                    $targets{$item}{config}{interval} ne '1') ||
                    (exists $targets{$item}{suppress} &&
                     $targets{$item}{suppress} =~ /h/ &&
                     $is_set_no_preview &&
                     $targets{$item}{config}{interval} eq '1') ) {
                push @graph_text, <<EOT;
<div>
<a name="$item_relative">&nbsp;</a><a
href="$item_relative.html$modified_href">$itemname</a>
&nbsp;&nbsp;&nbsp;&nbsp;$freqtext Graphic suppressed. More data is available
<a href="$item_relative.html">here</a>.
</div>
EOT
                next;
            };

            if( $is_set_no_preview ) {
                push @graph_text, <<EOT;
    <div>
    <a name="$item_relative">&nbsp;</a><a
     href="$item_relative.html$modified_href">$itemname</a>
    </div>
EOT
            }

            my $detailed_freq =
                ($targets{$item}{config}{interval} eq '1') ? 'hour' : 'day';
            push @graph_text, <<EOT;
<span>
    <a href="$item_relative.html$modified_href"><img
    src="$item_relative-$freq.$imagetype"
    width="$xsize" height="$ysize"
    class="tooltipTrigger"
    title="$itemname"
EOT
            if( !$is_set_no_preview ) {
                push @graph_text, <<EOT;
    onmouseover="return overlib(OLpreviewImage('$item_relative-$detailed_freq.$imagetype'), CAPTION, 'Detailed View for $itemname', WIDTH, 602);"
    onmouseout="nd();"
EOT
            }
            push @graph_text, <<EOT;
    alt="$itemname"/></a>
</span>
EOT
        } 
        if( $is_set_no_preview ) {
            push @nav_text, <<EOT;
            <ul class="listAsTable">
EOT
            foreach my $graph( @graphs ) {
                push @nav_text, <<EOT;
                <li>&raquo; <a href="#$graph->{item}">$graph->{name}</a></li>
EOT
            }
            push @nav_text, <<EOT;
            </ul>
            </div>
EOT
        }
    }
    print <<EOT;
    <div id="nav">
        <div id="menu">
            <h1 class="firstheading">Navigation</h1>
@nav_text
        </div>
    </div>
    <div id="graphs">
@graph_text
    </div>
EOT

    if( $is_set_no_preview and $summary->{graphs} ) {
        # print summary
        print '<div id="summary">';
        print '<h1>', $summary->{graphs}-$summary->{suppress}, ' Graph(s)</h1>';
        $summary->{subdir} and
            print '<h1>', $summary->{subdir},
                $summary->{subdir} > 1 ? ' Subdirectories' : ' Subdirectory',
                '</h1>';
        $summary->{suppress}
            and print '<p>', $summary->{suppress}, ' graph(s) suppressed</p>';
        print '</div>', "\n";
    }

    print '<div id="footer">', "\n";

    print <<EOT if @{$directories{$dir}{target}};
<b><a name="Archived">Archived Graphs</a></b>
<small>These are archived snapshots kept on the filesystem. Serving them
up via a web-viewable directory carries a very low performance hit.</small>
<br/>
Display of
<a href="?mode=daily">daily</a>,
<a href="?mode=monthly">monthly</a>,
<a href="?mode=yearly">yearly</a> archival modes is supported.
EOT

    print <<EOT;
<h3><a href="/rrd/scripts/">About This Site</a></h3>
<a href="http://www.rrdtool.org/"><img
    src="$resource_dir/rrdtool-logo-light.png" width="121"
    height="48" alt="RRDTool"/></a>
</div>
EOT

    print <<EOT;
</div>
@{[ SCRIPT_VERSION ]}
</body>
</html>
EOT
}

sub dump_targets() {
    for my $tgt (keys %targets) {
        print STDERR "Target $tgt:\n";
        for my $opt (keys %{$targets{$tgt}}) {
            print STDERR "    $opt: ", $targets{$tgt}{$opt}, "\n";
        }
    }
}

# forward declaration needed for recursive call
sub archive_directory($$);

# recursive subroutine to archive all targets in a directory
sub archive_directory($$) {
    my $dir = shift;
    $dir ||= '';            # default to top-level directory
    my $date = shift;
    $date ||= strftime "%m-%d-%Y", localtime;   # default to today
    if( exists $directories{$dir} ) {
        if( exists $directories{$dir}{target} ) {
            my( $archive_dir, $archive_url );
            if( !defined $directories{$dir}{config}{archivedir} ) {
                warn 'Undefined archivedir for ', $dir, '/', "\n";
                $archive_dir = '';
            } else {
                $archive_dir =
                    $directories{$dir}{config}{archivedir} . '/' . $dir;
            }
            if( !defined $directories{$dir}{config}{archivecgi} ) {
                die 'Undefined archivecgi for ', $dir, '/', "\n";
            } else {
                $archive_url =
                    $directories{$dir}{config}{archivecgi} . '/' . $dir;
            }

            unless( -d $archive_dir ) {
                eval { mkpath $archive_dir };
                if( $@ ) {
                    print_error("Could not create $archive_dir for $dir/: $@");
                    return;
                }
            }

            my( $m, $d, $y ) = split /-/, $date;

            # check to see if proper directory hierarchy exists
            # for directories with non-zero number of targets
            do {
                mkdir "$archive_dir/$y"
                    or die "mkdir $archive_dir/$y failed: $!"
                    unless -d "$archive_dir/$y";
                mkdir "$archive_dir/$y/$m"
                    or die "mkdir $archive_dir/$y/$m: failed $!";
            } unless !@{$directories{$dir}{target}} or
                        -d "$archive_dir/$y/$m";

            # user agent
            my $ua = new LWP::UserAgent;
            for my $target ( @{$directories{$dir}{target}} ) {
                my $target_relative;
                ( $target_relative = $target ) =~ s#$dir/?##;

                ## capture daily images
                # file location for storing image
                my $file = "$archive_dir/$y/$m/$target_relative-$y-$m-$d.$imagetype";
                # url
                my $url = "$archive_url/$target_relative-day.$imagetype";
                save_image_url($ua, $file, $url);

                ## capture monthly images if its the first day of the month
                if( $d eq '01' ) {
                    my( $save_y, $save_m );
                    if( $m ne '01' ) {
                        $save_m = $m - 1;
                        $save_y = $y;
                    } else {
                        # year rolled over to previous
                        $save_m = '12';
                        $save_y = $y - 1;
                    }
                    # add leading zero if less than 10
                    $save_m < 10 and $save_m = '0' . $save_m;
                    $file =
                        "$archive_dir/$save_y/$target_relative-$save_y-$save_m.$imagetype";
                    $url = "$archive_url/$target_relative-month.$imagetype";
                    save_image_url($ua, $file, $url);
                    ## capture yearly images if its the first day of the year
                    if( $m eq '01' ) {
                        $file = "$archive_dir/$target_relative-$save_y.$imagetype";
                        $url = "$archive_url/$target_relative-year.$imagetype";
                        save_image_url($ua, $file, $url);
                    }
                }
            }
        }
        if( exists $directories{$dir}{subdir} ) {
            for my $subdir ( @{$directories{$dir}{subdir}} ) {
                archive_directory(
                    $dir eq '' ? $subdir : $dir . '/' . $subdir,
                    $date);
            }
        }
    }
}

# save an image from a URL to a file location
sub save_image_url($$$) {
    my $ua = shift;         # user agent
    my $file = shift;       # file location for saving image
    my $url = shift;        # url to get

    # request
    my $req = GET $url;
    # repsonse
    my $res = $ua->request($req, $file);
    die 'Error while getting ' . $res->request->uri
            . ' ' . $res->status_line
        unless $res->is_success;
}

# forward declaration needed for recursive call
sub dump_directories($$);

# recursive subroutine to print all directories
sub dump_directories($$) {
    my $dir = shift;
    my $indent = shift;
    $dir ||= '';            # default to top-level directory
    $indent ||= 0;
    print STDERR '    ' x $indent, 'Directory: ', $dir, '/', "\n";
    if( exists $directories{$dir} ) {
        for my $target ( @{$directories{$dir}{target}} ) {
            print STDERR '    ' x $indent, '    Target: ', $target, "\n";
        }
        for my $subdir ( @{$directories{$dir}{subdir}} ) {
            dump_directories($subdir, $indent+1);
        }
    }
}

sub print_error(@)
{
    print <<EOT;
Content-Type: text/html; charset=iso-8859-1

@{[HTML_PREAMBLE]}
<head><title>Error</title></head>
<body>
<p>
Error: 
EOT
    print join(' ', @_), "\n";
    print <<EOT;
</p>
</body>
</html>
EOT
    exit 0;
}

my $q = $ENV{MOD_PERL} ? CGI->new(shift @_) : CGI->new();
main($q);

1;

