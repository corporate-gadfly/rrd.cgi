#!/usr/bin/perl -w
#
# rrd.cgi: The script for generating graphs for rrdtool statistics.
#
# Author: Haroon Rafique <haroon.rafique@utoronto.ca>
#
# Closely modelled after the Jan "Yenya" Kasprzak <kas@fi.muni.cz>'s
# mrtg-rrd.cgi available at: http://www.fi.muni.cz/~kas/mrtg-rrd/
# I didn't like its limitations and tight coupling with MRTG
#
# $Id$

use strict;

use POSIX qw(strftime);
use Time::Local;
use Text::ParseWords;
use Date::Manip;
use CGI;
use LWP::UserAgent;
use HTTP::Request::Common qw(GET);

# Force 5.8.0 because of different handling of %.1f/%.1lf in sprintf() in 5.6.x
require 5.008;

use RRDs;

use vars qw(@config_files @all_config_files %targets $config_time
    %directories $imagetype $percent_h);

# EDIT THIS to reflect all your RRD config files
BEGIN { @config_files = qw(
    /etc/rrd/rrd.cfg
    /etc/rrd/rrd-tomcat.cfg
    /etc/rrd/rrd-network.cfg
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
        /^(.*)\/([^\/]+)(\.html|-(hour|day|week|month|year)\.($imagetype|src))$/);

    $dir && $dir =~ s/^\///;

    print_error('Undefined statistic: ' . $q->path_info())
        unless defined $stat and defined $targets{$stat};

    print_error('Incorrect directory: ' . $q->path_info())
        unless defined $targets{$stat}{directory} ||
        $targets{$stat}{directory} eq $dir;

    my $tgt = $targets{$stat};

    common_args($stat, $tgt, $q);

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

    my( $avh, $xh, $yh ) = do_image($tgt, 'hour',   0, 0)
        unless $tgt->{suppress} =~ /h/ or
        $tgt->{config}{interval} ne '1';
    my( $avd, $xd, $yd ) = do_image($tgt, 'day',   0, 0);
    my( $avw, $xw, $yw ) = do_image($tgt, 'week',  0, 0);
    my( $avm, $xm, $ym ) = do_image($tgt, 'month', 0, 0);
    my( $avy, $xy, $yy ) = do_image($tgt, 'year',  0, 0);

            # change the refresh interval only if hourly is enabled
    $tgt->{config}{refresh} = 60
        if $tgt->{config}{interval} eq '1' and $tgt->{suppress} !~ /h/;
    http_headers('text/html', $tgt->{config});
    print <<EOT;
<html>
<head>
<link type="text/css" rel="stylesheet" href="$tgt->{config}{icondir}/style.css">
<title>
EOT
    print $tgt->{title} if defined $tgt->{title};
    print <<EOT;
</title>
</head>
<body bgcolor=#ffffff>
<table border="0">
     <tr align="left" valign="top">
         <td>
EOT

    print $tgt->{pagetop} if defined $tgt->{pagetop};

    my $mtime = (stat $tgt->{rrd})[9];

    if( !defined $mtime ) {
        $mtime = 0;
        print STDERR
            'Could not get status info for ', $tgt->{rrd}, '. ',
            'Missing symbolic link or incorrect permissions!', "\n";
    }
    print "The statistics were last updated ",
        strftime("<b>%A, %d %B, %H:%M:%S %Z</b>\n",
            localtime($mtime));
    my $no_auto_refresh_href =
        ($q->param('autorefresh') and
        $q->param('autorefresh') eq 'no')
            ?
        '?autorefresh=no'
            :
        '';
    my $switch_auto_refresh =
        $no_auto_refresh_href
        ?
        '<a href="' . $q->url(-absolute=>1,-path=>1) . '">Autorefresh version of this page</a>'
        :
        '<a href="?autorefresh=no">Non-autorefresh version of this page</a>';
    print <<EOT;
<p>
<small>Scroll to:
@{[ ($tgt->{suppress} =~ /h/ or $tgt->{config}{interval} ne '1') ? '' : '<a href="#Hourly">Hourly</a>|' ]}
@{[ $tgt->{suppress} =~ /d/ ? '' : '<a href="#Daily">Daily</a>|' ]}
@{[ $tgt->{suppress} =~ /w/ ? '' : '<a href="#Weekly">Weekly</a>|' ]}
@{[ $tgt->{suppress} =~ /m/ ? '' : '<a href="#Monthly">Monthly</a>|' ]}
@{[ $tgt->{suppress} =~ /y/ ? '' : '<a href="#Yearly">Yearly</a>|' ]}
<a href="#Historical">Historical</a>|
<a href="#Archived">Archived</a> Graphs</small>
<br>
<small>Go: <a href="./$no_auto_refresh_href">up to parent level</a>, or<br>
Go to $switch_auto_refresh.</small>
        </td>
        <td style="padding-top: 50px;">
EOT

                # total number of graphs (either 4 or 5)
    my $total_graphs = $tgt->{config}{interval} ne '1' ? 4 : 5;
                # How many are suppressed?
    my( $suppressed_graphs ) = $tgt->{suppress} =~ /([hdwmy]+)/;
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

    html_graph($tgt, 'hour', 'Hourly', $dayavg . ' Minute', $xh, $yh, $avh);
    html_graph($tgt, 'day', 'Daily', '5 Minute', $xd, $yd, $avd);
    html_graph($tgt, 'week', 'Weekly', '30 Minute', $xw, $yw, $avw);
    html_graph($tgt, 'month', 'Monthly', '2 Hour', $xm, $ym, $avm);
    html_graph($tgt, 'year', 'Yearly', '1 Day', $xy, $yy, $avy);

    print <<EOT;
        </td>
    </tr>
</table>
<br>
<b><a name="Historical">Run-time Historical Graphs</a></b>
<small>These historical graphs produce images that are not cached at
all and hence carry a performance hit every time they are requested,
so be gentle</small>
<br>
EOT
    foreach my $i (1..6) {
        last if $tgt->{suppress} =~ /h/ or
            $tgt->{config}{interval} ne '1';
        print '<a href="?start=', -$i, 'h">',
            $i, ' hour', $i > 1 ? 's' : '', ' ago',
            '</a>', "\n";
    }
    print '<br>', "\n";
    foreach my $i (1..7) {
        print '<a href="?start=', -$i, 'd">',
            $i, ' day', $i > 1 ? 's' : '', ' ago',
            '</a>', "\n";
    }
    print '<br>', "\n";
    foreach my $i (1..4) {
        print '<a href="?start=', -$i, 'w">',
            $i, ' week', $i > 1 ? 's' : '', ' ago',
            '</a>', "\n";
    }
    print '<br>', "\n";
    foreach my $i (1..6) {
        print '<a href="?start=', -$i, 'm">',
            $i, ' month', $i > 1 ? 's' : '', ' ago',
            '</a>', "\n";
    }
    print '<br>', "\n";
    print <<EOT;
<form method="post">
Arbitrary start and end dates:<br>
Start Date: <input type="text" name="start" length="6" maxlength="40">
End Date: <input type="text" name="end" length="6" maxlength="40">
<input type="submit">
</form>
<small><dl>
<dt>Some examples of date specification for the above 2 inputs are:
<dd>today
<dd>1st thursday in June 1992
<dd>05/10/93
<dd>April 1, 2003
<dd>2 days ago
<dd>15 weeks ago
<dd>..., etc.
</dl>
</small>
EOT

    print <<EOT;
<br>
<b><a name="Archived">Archived Graphs</a></b>
<small>These are archived snapshots kept on the filesystem. Serving them
up via a web-viewable directory carries a very low performance hit.</small>
<br>
Display of
<a href="?mode=daily">daily</a>,
<a href="?mode=monthly">monthly</a>,
<a href="?mode=yearly">yearly</a> archival modes is supported.
<br>
EOT

    print <<EOT;
<a href="http://www.rrdtool.org/"><img
    src="$tgt->{config}{icondir}/rrdtool.gif" width="120"
    height="34" alt="RRDTool" border="0"></a>
EOT

    print '<!-- $Id$ -->', "\n";
    print <<EOT;
</body>
</html>
EOT

}

sub html_graph($$$$$$$)
{
    my ($tgt, $ext, $freq, $period, $xsize, $ysize, $av) = @_;

    return unless defined $tgt->{$ext};

    print <<EOT;
<br><a name="$freq"><b>"$freq" Graph ($period Average)</b></a><br>
<img src="$tgt->{url}-$ext.$imagetype"
width="$xsize" height="$ysize"
alt="$freq Graph" vspace="10" align="top"><br>
EOT
    if( defined $av->[0] ) {
        print "<small>";
        print defined $tgt->{relpercent} ?
            $tgt->{relpercent} : "Relative percentage";
        print ' Cur: ', $av->[1] != 0 ?
            sprintf('%.1f%%', $av->[0]/$av->[1]*100) : ' 0.0%';
        print ' Avg: ', $av->[3] != 0 ?
            sprintf('%.1f%%', $av->[2]/$av->[3]*100) : ' 0.0%';
        print ' Max: ', $av->[5] != 0 ? 
            sprintf('%.1f%%', $av->[4]/$av->[5]*100) : ' 0.0%';
        print "</small><br>";
    }

    print <<EOT;
<small><a href="$tgt->{url}-$ext.src">[source]</a></small>
EOT
}

sub http_headers($$)
{
    my ($content_type, $cfg) = @_;
    my $interval = $cfg->{interval};
    $interval ||= 5;
    my $refresh = $cfg->{refresh};
    $refresh ||= 300;

    print 'Content-Type: ', $content_type, "\n";

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
    my ($target, $ext, $wantsrc, $wantimage) = @_;

    my $file = $target->{$ext};

    do {
        print_error("Target '$ext' suppressed for this target") if $wantimage;
        return;
    } unless defined $file;

    # Now the vertical rule at the end of the day
    my @t = localtime(time);
    # set seconds, minutes, hours to zero
    $t[0] = $t[1] = $t[2] = 0 unless $ext eq 'hour';

    my $seconds;
    my $oldsec;
    my $back;
    my $xgrid = '';

    if ($ext eq 'hour') {
        $seconds = timelocal(@t);
        $back = 3*3600;     # 3 hours
        $oldsec = $seconds - $t[2]*3600 - $t[1]*60 - $t[0];     # FIXME: where to set the VRULE
        $seconds = 0;
    } elsif ($ext eq 'day') {
        $seconds = timelocal(@t);
        $back = 30*3600;    # 30 hours
        $oldsec = $seconds - 86400;
        # We need this only for day graph. The other ones
        # are magically correct.
        $xgrid = 'HOUR:1:HOUR:6:HOUR:2:0:' . $percent_h;
    } elsif ($ext eq 'week') {
        $seconds = timelocal(@t);
        $t[6] = ($t[6]+6) % 7;
        $seconds -= $t[6]*86400;
        $back = 8*86400;    # 8 days
        $oldsec = $seconds - 7*86400;
    } elsif ($ext eq 'month') {
        $t[3] = 1;
        $seconds = timelocal(@t);
        $back = 36*86400;   # 36 days
        $oldsec = $seconds - 30*86400; # FIXME (the right # of days!!)
    } elsif ($ext eq 'year') {
        $t[3] = 1;
        $t[4] = 0;
        $seconds = timelocal(@t);
        $back = 396*86400;  # 365 + 31 days
        $oldsec = $seconds - 365*86400; # FIXME (the right # of days!!)
    } else {
        print_error("Unknown file extension: $ext");
    }

    my @local_args;

    if ($xgrid) {
        push @local_args, '-x', $xgrid;
    }

    my @graph_args = get_graph_args($target);
    if( exists $target->{percentilevalue} ) {
        my @percentile = calc_percentile($target, -$back, 'now');
        my @ds = split / +/, $target->{percentilesources};
        foreach my $i(0 .. (scalar @ds)-1) {
            for( @graph_args ) {
                s/%PERCENTILE${i}%/$percentile[$i]/g;
                s/%PERCENTILEVALUE%/$target->{percentilevalue}/g;
            }
        }
    }
    do {
        http_headers("text/html", $target->{config});
        print '<pre>RRDs::graph(',
                join(",\n",
                $file, '-s', "-$back", @local_args,
                @{$target->{args}}, @graph_args, "VRULE:$oldsec#ff0000",
                "VRULE:$seconds#ff0000"),
                ')</pre>';
        return;
    } if $wantsrc;

    my( $averages, $xsize, $ysize ) =
        RRDs::graph($file, '-s', "-$back", @local_args,
        @{$target->{args}}, @graph_args, "VRULE:$oldsec#ff0000",
        "VRULE:$seconds#ff0000");

    my $rrd_error = RRDs::error;
    print_error("RRDs::graph failed, $rrd_error") if defined $rrd_error;

    # Do not proceed unless image is wanted
    return( $averages, $xsize, $ysize ) unless $wantimage;

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

sub calc_percentile($$$) {
    use Statistics::Descriptive;
    my $target = shift;     # target
    my $start = shift;      # start time
    my $end = shift;        # end time
                            # sources separated by spaces
    my @ds = split / +/, $target->{percentilesources};
    my( undef, undef, undef, $data ) = RRDs::fetch($target->{rrd},
            'AVERAGE',
            '-s', $start,
            '-e', $end);
    my( @averages );
    my( @percentile );
    $target->{percentilemultiplier} = 1
        unless exists $target->{percentilemultiplier};
    foreach my $i(0 .. (scalar @ds)-1) {
        my $compound = 0;
        $compound = 1 if $ds[$i] =~ /\+/; 
DATAPOINT: {
            foreach my $d( @$data ) {
                if( $compound ) {
                    my $val = 0;
                    foreach my $index(split /\+/, $ds[$i]) {
                        next DATAPOINT unless defined $d->[$index];
                        $val += $d->[$index];
                    }
                    push @{$averages[$i]}, $val;
                } else {
                        # only add defined values to array
                    next unless defined $d->[$ds[$i]];
                    push @{$averages[$i]}, $d->[$ds[$i]];
                }
            }
        }
 
        # empty percentile value if averages array is empty
        do {
            $percentile[$i] = 0;
            next;
        } unless exists $averages[$i] && scalar @{$averages[$i]};
        my $stat = Statistics::Descriptive::Full->new();
        $stat->add_data(@{$averages[$i]});
                    # get percentile for given data
        $percentile[$i] =
            $stat->percentile($target->{percentilevalue})*
            $target->{percentilemultiplier};
        $percentile[$i] = sprintf("%.1f", $percentile[$i]);
    }
    return @percentile;
}
 
sub get_graph_args($) {
    my $target = shift;
            # Use space as a delimeter to break up {graph} into a list
            # of words ignoring spaces inside quotes.
    my @graph_args = ();
    @graph_args =
            # eliminate all quotes and replace '\ ' with ' '
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
            if ($start_time-$end_time) <= 86400;
    } elsif( defined $start ) {
        my( $interval, $type ) = ($start =~ m/(\-\d+)([hdwm])/);
                # regular -1d, -1m, -2w style start interval with no end
        if( defined $interval && defined $type ) {
                # work around a bug in RRD's time parsing code which
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
    if( exists $target->{percentilevalue} ) {
        my @percentile = calc_percentile($target, $start_time, $end_time);
        my @ds = split / +/, $target->{percentilesources};
        foreach my $i(0 .. (scalar @ds)-1) {
            for( @graph_args ) {
                s/%PERCENTILE${i}%/$percentile[$i]/g;
                s/%PERCENTILEVALUE%/$target->{percentilevalue}/g;
            }
        }
    }
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

    return @{$target->{args}} if defined @{$target->{args}};

    $target->{name} = $name;

    $target->{directory} = ''
        unless defined $target->{directory};

    my $tdir = $target->{directory};
    $tdir .= '/'
        unless $tdir eq '' || $tdir =~ /\/$/;

    $target->{url} = $q->url . '/' . $tdir . $name;

    my $cfg = $target->{config};

    my $autorefresh = $q->param('autorefresh') || '';
    $cfg->{autorefresh} = 'no' if $autorefresh eq 'no';

    my $dir = $cfg->{workdir};
    $dir = $cfg->{logdir}
        if defined $cfg->{logdir};

    $target->{rrd} = $dir . '/' . $tdir . $name . '.rrd';

    $dir = $cfg->{workdir};
    $dir = $cfg->{imagedir}
        if defined $cfg->{imagedir};

    $target->{suppress} ||= '';

    $target->{hour}   = $dir . '/' . $tdir . $name
        . '-hour.' . $imagetype unless
        $target->{suppress} =~ /h/ or $cfg->{interval} ne '1';
    $target->{day}   = $dir . '/' . $tdir . $name
        . '-day.' . $imagetype unless $target->{suppress} =~ /d/;
    $target->{week}  = $dir . '/' . $tdir . $name
        . '-week.' . $imagetype unless $target->{suppress} =~ /w/;
    $target->{month} = $dir . '/' . $tdir . $name
        . '-month.' . $imagetype unless $target->{suppress} =~ /m/;
    $target->{year}  = $dir . '/' . $tdir . $name
        . '-year.' . $imagetype unless $target->{suppress} =~ /y/;

    my @args = ();

    push @args, '--lazy',
        '-a', uc $imagetype,
        '-h', '120',
        '-w', '500',
        '-c', 'FONT#000000',
        '-c', 'MGRID#000000',
        '-c', 'FRAME#000000',
        '-c', 'BACK#f5f5f5',
        '-c', 'ARROW#000000';

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
            # no date provided - so default to yesterday
        ( $m, $d, $y ) = UnixDate('yesterday', '%m', '%d', '%Y');
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
    $dir =~ s/^\///g;

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
        push @targets, $stat;
        $title = 'Image for ' . $stat;
    }

    for( $mode ) {
        /daily/     && do { $title .= " daily mode for $m-$d-$y"; last; };
        /monthly/   && do { $title .= " monthly mode for $m-$y"; last; };
        /yearly/    && do { $title .= " yearly mode for $y"; last; };
    }

    my $icon_dir = defined $directories{$dir}{config}{icondir}
        ?
            $directories{$dir}{config}{icondir}
        :
            $directories{$directories{$dir}{subdir}[0]}{config}{icondir};
    http_headers('text/html', undef);
    print <<EOT;
<html>
<head>
<link type="text/css" rel="stylesheet" href="$icon_dir/style.css">
<title>RRD: Archived $title</title>
<script type="text/javascript" src="$icon_dir/CalendarPopup.js">
</script>
</head><body bgcolor="#ffffff">
EOT

    generate_calendar($mode, $m, $d, $y, $icon_dir);

    print 'Switch mode to:';
    for my $m ('daily', 'monthly', 'yearly') {
        print $mode eq $m
            ?
            ' ' . $m
            :
            ' [<a href="?mode=' . $m . '">' . $m . '</a>]';
    }
    print '<br>';


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
                    '</b><br> Suppressed for <b>', $mode,
                    '</b> archive mode: <br>';
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
                    $error_date, '</b>.<br>';
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
                      '</b>.<br>', "\n";
            }
            if( $mode eq 'yearly' and $cur_y <= $y ) {
                print 'It will become available on <b>',
                      '01-01-', $y+1,
                      '</b>.<br>', "\n";
            }
            next;
        }
        print <<EOT;
<b>$targets{$target}{title}</b>
<br>
<img src="$archive_url/$dir/$image_file">
<br>
EOT
    }
    print '<!-- $Id$ -->', "\n";
    print <<EOT;
</body>
</html>
EOT
}

# generate code for JavaScript calendar
#   remember that, in JavaScript, the 2nd argument to
#   Date($y,@{[$m-1]},$d) needs to have 1 subtracted from it as the
#   JavaScript months go from 0 to 11
sub generate_calendar($$$$$) {
    my $mode = shift;
    my $m = shift;
    my $d = shift;
    my $y = shift;
    my $icon_dir = shift;

    print <<EOT;
<script type="text/javascript" language="JavaScript">
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

/* since calendar is now in a div, let's print the necessary css */
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
</script>

<form method="post">
    <input style="margin-left: 75px;" type="text" name="date"
EOT

    print <<EOT if $mode eq 'daily';
        value="$m-$d-$y" size="10">
EOT
    print <<EOT if $mode eq 'monthly';
        value="$m-$y" size="7">
EOT
    print <<EOT if $mode eq 'yearly';
        value="$y" size="4">
EOT
    print <<EOT;
    <a href="#"
        onClick="cal.showCalendar(this.id); return false;"
        name="calAnchor" id="calAnchor"><img
        width="34" height="21" border="0"
        src="$icon_dir/calendar.gif"></a>
</form>
<div id="calDiv"
    style="position:absolute; visibility:hidden; background-color:white;layer-background-color:white;"></div>
EOT
}

sub try_read_config($)
{
    my ($prefix) = (@_);
    $prefix =~ s/\/[^\/]*$//;

    # Verify the version of RRDtool:
    if (!defined $RRDs::VERSION || $RRDs::VERSION < 1.000331) {
        print_error("Please install more up-to date RRDtool - need at least 1.000331");
    }
    
    my $read_cfg;
    if (!defined $config_time) {
        $read_cfg = 1;
    } else {
        for my $file (@all_config_files) {
            my $mtime = (stat $file)[9];
            if ($config_time < $mtime) {
                $read_cfg = 1;
                last;
            }
        }
    }

    return unless $read_cfg;

    %targets = ();

    @all_config_files = @config_files;

    my $order = 0;
    for my $cfgfile (@config_files) {
        my $cfgref = {
            refresh => 300,
            interval => 5,
            icondir => $prefix
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
        s/\s+/ /g;                # collapse white spaces to ' '
        next if /^ *\#/;           # skip comment lines
        next if /^\s*$/;          # skip empty lines
        if (/^ \S/) {             # multiline options
            $lines[$#lines] .= $_;
        } else {
            push @lines, $_;
        }
    }
    close CFG;

    foreach (@lines) {
        if (/^\s*([\w\d]+)\[(\S+)\]\s*:\s*(.*)$/) {
            my ($opt, $tgt, $val) = (lc($1), lc($2), $3);
            unless (exists $targets{$tgt}) {
                $targets{$tgt}{name} = $tgt;
                $targets{$tgt}{directory} = '';
                $targets{$tgt}{order} = ++$$order;
                $targets{$tgt}{config} = $cfgref;
            }
            $targets{$tgt}{$opt} = $val;
            next;
        } elsif (/^([\w\d]+)\s*:\s*(\S.*)$/) {
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

    for my $name (@names) {
        my $dir = $targets{$name}{directory}
            if defined $targets{$name}{directory};
        $dir = '' unless defined $dir;

        my $prefix = '';
        for my $component (split /\/+/, $dir) {
            unless (defined $directories{$prefix.$component}) {
                push (@{$directories{$prefix}{subdir}},
                    $component);
            }
            if( $prefix eq '' ) {
                # with an empty prefix, use the component itself as the
                # next prefix
                $prefix = $component;
            } else {
                $prefix .= $component . '/';
            }
        }
        push (@{$directories{$dir}{target}}, $name);
    }
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
    # has interval eq '1' meaning a refresh of 60
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

    my $icon_dir = defined $directories{$dir}{config}{icondir}
        ?
            $directories{$dir}{config}{icondir}
        :
            $directories{$directories{$dir}{subdir}[0]}{config}{icondir};
    print <<EOT;
<html>
<head>
<link type="text/css" rel="stylesheet" href="$icon_dir/style.css">
<title>RRD: Directory $dir1</title>
</head><body bgcolor=#ffffff>
<table border="0">
    <tr align="left" valign="top">
        <td>
EOT

    my $no_auto_refresh_href =
        ($q->param('autorefresh') and
        $q->param('autorefresh') eq 'no')
            ?
        '?autorefresh=no'
            :
        '';

    my( @graphs, @text );
    if (defined @{$directories{$dir}{subdir}}) {
        print <<EOT;
<h1>RRD subdirectories in $dir1</h1>
<small>More graphs are available in the following subdirectories</small>

<ul>
EOT
        for my $item (@{$directories{$dir}{subdir}}) {
            print "<li><a href=\"$item/$no_auto_refresh_href\">$item/</a>\n";
            $summary->{subdir}++;
        }

        print '</ul>', "\n";
    }

    # print summary
    print '<div id="summary">';
    $summary->{graphs} and
        print '<h1>', $summary->{graphs}-$summary->{suppress}, ' Graph(s)</h1>';
    $summary->{subdir} and
        print '<h1>', $summary->{subdir},
            $summary->{subdir} > 1 ? ' Subdirectories' : ' Subdirectory',
            '</h1>';
    $summary->{suppress}
        and print '<p>', $summary->{suppress}, ' graph(s) suppressed</p>';
    print '</div>';

    if (defined @{$directories{$dir}{target}}) {
        my $switch_auto_refresh =
            $no_auto_refresh_href
            ?
            '<a href="' . $q->url(-absolute=>1,-path=>1) . '">Autorefresh version of this page</a>'
            :
            '<a href="?autorefresh=no">Non-autorefresh version of this page</a>';
        print <<EOT;
<h1>RRD graphs in $dir1</h1>
<small>Click on a graphic to the right to go to a deeper level, or<br>
Go up to <a href="../$no_auto_refresh_href">parent level</a>, or<br>
Go to $switch_auto_refresh.</small>
EOT

        for my $item (@{$directories{$dir}{target}}) {
            my $itemname = $item;
            common_args($item, $targets{$item}, $q);
            my( $freq, $freqtext );
            if( $targets{$item}{config}{interval} eq '1' ) {
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
            push @graphs, {item => $item, name => $itemname};
            if( ($targets{$item}{suppress} =~ /d/ &&
                    $targets{$item}{config}{interval} ne '1') ||
                    ($targets{$item}{suppress} =~ /h/ &&
                     $targets{$item}{config}{interval} eq '1') ) {
                push @text, <<EOT;
<tr>
<td><a name="$item">&nbsp;</a><a href="$item.html$no_auto_refresh_href">$itemname</a><br>
&nbsp;&nbsp;&nbsp;&nbsp;$freqtext Graphic suppressed. More data is available
<a href="$item.html">here</a>.
</tr>
EOT
                next;
            };
            push @text, <<EOT;
<tr>
   <td><a name="$item">&nbsp;</a><a
    href="$item.html$no_auto_refresh_href">$itemname</a><br>
    <a href="$item.html$no_auto_refresh_href"><img src="$item-$freq.$imagetype"
    width="$xsize" height="$ysize"
    border="0" align="top" vspace="10" alt="$item"></a><br clear="all">
   </td>
</tr>
EOT
        } 
        print '<ul>', "\n";
        foreach my $graph( @graphs ) {
            print <<EOT;
<li><a href="#$graph->{item}">$graph->{name}</a>
EOT
        }
        print '</ul></td><td style="padding-top: 60px;">', "\n";
        print '<table border=0 width=100%>', "\n";
        print @text;
        print "</table>\n";
    } else {
        print '</td><td style="padding-top: 60px;">&nbsp;', "\n";
    }
    print '</td></tr></table>', "\n";

    print <<EOT if @{$directories{$dir}{target}};
<br>
<b><a name="Archived">Archived Graphs</a></b>
<small>These are archived snapshots kept on the filesystem. Serving them
up via a web-viewable directory carries a very low performance hit.</small>
<br>
Display of
<a href="?mode=daily">daily</a>,
<a href="?mode=monthly">monthly</a>,
<a href="?mode=yearly">yearly</a> archival modes is supported.
<br>
EOT

    print <<EOT;
<h3><a href="/rrd/special/">Issues/Problem events</a> | <a
    href="/rrd/scripts/">About This Site</a></h3>
<a href="http://www.rrdtool.org/"><img
    src="$icon_dir/rrdtool.gif" width="120"
    height="34" alt="RRDTool" border="0"></a>
EOT

    print '<!-- $Id$ -->', "\n";
    print <<EOT;
</body>
</html>
EOT
}

sub dump_targets() {
    for my $tgt (keys %targets) {
        print "Target $tgt:\n";
        for my $opt (keys %{$targets{$tgt}}) {
            print "\t$opt: ", $targets{$tgt}{$opt}, "\n";
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
                warn 'Nonexistent directory ', $archive_dir, ' for ',
                     $dir, '/', "\n";
                return;
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

                ## capture daily images
                # file location for storing image
                my $file = "$archive_dir/$y/$m/$target-$y-$m-$d.$imagetype";
                # url
                my $url = "$archive_url/$target-day.$imagetype";
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
                        "$archive_dir/$save_y/$target-$save_y-$save_m.$imagetype";
                    $url = "$archive_url/$target-month.$imagetype";
                    save_image_url($ua, $file, $url);
                    ## capture yearly images if its the first day of the year
                    if( $m eq '01' ) {
                        $file = "$archive_dir/$target-$save_y.$imagetype";
                        $url = "$archive_url/$target-year.$imagetype";
                        save_image_url($ua, $file, $url);
                    }
                }
            }
        }
        if( exists $directories{$dir}{subdir} ) {
            for my $subdir ( @{$directories{$dir}{subdir}} ) {
                archive_directory($subdir, $date);
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
    print '    ' x $indent, 'Directory: ', $dir, '/', "\n";
    if( exists $directories{$dir} ) {
        for my $target ( @{$directories{$dir}{target}} ) {
            print '    ' x $indent, '    Target: ', $target, "\n";
        }
        for my $subdir ( @{$directories{$dir}{subdir}} ) {
            dump_directories($subdir, $indent+1);
        }
    }
}

sub print_error(@)
{
    print "Content-Type: text/html\n\nError: ", join(' ', @_), "\n";
    exit 0;
}

my $q;
if( $ENV{MOD_PERL} ) {
    my $r = shift;
    $q = new CGI($r);
} else {
    $q = new CGI;
}
main($q);

1;

