#!/usr/bin/perl -w
#
# mrtg-rrd.cgi: The script for generating graphs for rrdtool statistics.
#
# Closely modelled after the Jan "Yenya" Kasprzak <kas@fi.muni.cz>'s
# mrtg-rrd.cgi
#

use strict;

use POSIX qw(strftime);
use Time::Local;
use Text::ParseWords;
use Date::Manip;

# Force 5.8.0 because of different handling of %.1f/%.1lf in sprintf() in 5.6.x
require 5.008;

# Location of RRDs.pm, if it is not in @INC
# use lib '/usr/lib/perl5/5.00503/i386-linux';
use RRDs;

use vars qw(@config_files @all_config_files %targets $config_time
	%directories $version $imagetype);

# EDIT THIS to reflect all your MRTG config files
BEGIN { @config_files = qw(/etc/mrtg/rrd.cfg); }

$version = '0.6';

# This depends on what image format your libgd (and rrdtool) uses
$imagetype = 'png'; # or make this 'gif';

sub handler ($)
{
	my ($q) = @_;

	try_read_config($q->url());

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
		/^(.*)\/([^\/]+)(\.html|-(day|week|month|year)\.($imagetype|src))$/);

	$dir =~ s/^\///;

	print_error("Undefined statistics")
		unless defined $targets{$stat};

	print_error("Incorrect directory")
		unless defined $targets{$stat}{directory} || $targets{$stat}{directory} eq $dir;

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
		do_html($tgt);
	} elsif ($ext eq '-day.' . $imagetype) {
		do_image($tgt, 'day', 0, 1);
	} elsif ($ext eq '-week.' . $imagetype) {
		do_image($tgt, 'week', 0, 1);
	} elsif ($ext eq '-month.' . $imagetype) {
		do_image($tgt, 'month', 0, 1);
	} elsif ($ext eq '-year.' . $imagetype) {
		do_image($tgt, 'year', 0, 1);
	} elsif ($ext eq '-day.src') {
		do_image($tgt, 'day', 1, 0);
	} elsif ($ext eq '-week.src') {
		do_image($tgt, 'week', 1, 0);
	} elsif ($ext eq '-month.src') {
		do_image($tgt, 'month', 1, 0);
	} elsif ($ext eq '-year.src') {
		do_image($tgt, 'year', 1, 0);
	} else {
		print_error("Unknown extension");
	}
	$ENV{TZ} = $oldtz
		if defined $oldtz;
}

sub do_html($)
{
	my ($tgt) = @_;

	my( $avd, $xd, $yd ) = do_image($tgt, 'day',   0, 0);
	my( $avw, $xw, $yw ) = do_image($tgt, 'week',  0, 0);
	my( $avm, $xm, $ym ) = do_image($tgt, 'month', 0, 0);
	my( $avy, $xy, $yy ) = do_image($tgt, 'year',  0, 0);

	http_headers('text/html', $tgt->{config});
	print <<EOT;
<HTML>
<HEAD>
<link type="text/css" rel="stylesheet" href="$tgt->{config}{icondir}/style.css">
<TITLE>
EOT
	print $tgt->{title} if defined $tgt->{title};
	print "</TITLE>\n";

	print "</HEAD>\n<BODY BGCOLOR=#ffffff>\n";
	
	print $tgt->{pagetop} if defined $tgt->{pagetop};

    my @st = stat $tgt->{rrd};

    print "The statistics were last updated ",
        strftime("<B>%A, %e %B, %T %Z</B>\n",
            localtime($st[9]));
    print <<EOT;
<p>
<small>Scroll to:
@{[ $tgt->{suppress} =~ /d/ ? '' : '<a href="#Daily">Daily</a>|' ]}
@{[ $tgt->{suppress} =~ /w/ ? '' : '<a href="#Weekly">Weekly</a>|' ]}
@{[ $tgt->{suppress} =~ /m/ ? '' : '<a href="#Monthly">Monthly</a>|' ]}
@{[ $tgt->{suppress} =~ /y/ ? '' : '<a href="#Yearly">Yearly</a>|' ]}
<a href="#Historical">Historical</a> Graphs</small>
<br>
EOT

	my $dayavg = $tgt->{config}->{interval};

#    print '<!--';
#    use Data::Dumper;
#    print Dumper(%targets);
#    print '-->', "\n";

	html_graph($tgt, 'day', 'Daily', $dayavg . ' Minute', $xd, $yd, $avd);
	html_graph($tgt, 'week', 'Weekly', '30 Minute', $xw, $yw, $avw);
	html_graph($tgt, 'month', 'Monthly', '2 Hour', $xm, $ym, $avm);
	html_graph($tgt, 'year', 'Yearly', '1 Day', $xy, $yy, $avy);

    print <<EOT;
<h4><a name="Historical">Historical Graphs</a></h4>
<small>These historical graphs produce images that are not cached at
all and hence carry a performance hit every time they are requested,
so be gentle</small>
<br>
EOT
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
<a href="http://www.rrdtool.org/"><img
    src="$tgt->{config}{icondir}/rrdtool.gif" width="120"
    height="34" alt="RRDTool" border="0"></a>
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

    print <<EOT;
Content-Type: $content_type
Pragma: no-cache
EOT
    # Don't print refresh headers for graphics
    print <<EOT unless $content_type eq "image/$imagetype";
Refresh: $cfg->{refresh}
EOT

	# Expires header calculation stolen from CGI.pm
	print strftime("Expires: %a, %d %b %Y %H:%M:%S GMT\n",
		gmtime(time+60*$cfg->{interval}));

	print "\n";
}

sub do_image($$$$)
{
	my ($target, $ext, $wantsrc, $wantimage) = @_;

	my $file = $target->{$ext};

	return unless defined $file;

	# Now the vertical rule at the end of the day
	my @t = localtime(time);
	$t[0] = $t[1] = $t[2] = 0;

	my $seconds;
	my $oldsec;
	my $back;
	my $xgrid = '';

	if ($ext eq 'day') {
		$seconds = timelocal(@t);
		$back = 30*3600;	# 30 hours
		$oldsec = $seconds - 86400;
		# We need this only for day graph. The other ones
		# are magically correct.
		$xgrid = 'HOUR:1:HOUR:6:HOUR:2:0:%-H';
	} elsif ($ext eq 'week') {
		$seconds = timelocal(@t);
		$t[6] = ($t[6]+6) % 7;
		$seconds -= $t[6]*86400;
		$back = 8*86400;	# 8 days
		$oldsec = $seconds - 7*86400;
	} elsif ($ext eq 'month') {
		$t[3] = 1;
		$seconds = timelocal(@t);
		$back = 36*86400;	# 36 days
		$oldsec = $seconds - 30*86400; # FIXME (the right # of days!!)
	} elsif ($ext eq 'year') {
		$t[3] = 1;
		$t[4] = 0;
		$seconds = timelocal(@t);
		$back = 396*86400;	# 365 + 31 days
		$oldsec = $seconds - 365*86400; # FIXME (the right # of days!!)
	} else {
		print_error("Unknown file extension: $ext");
	}

	my @local_args;

	if ($xgrid) {
		push @local_args, '-x', $xgrid;
	}

    my @graph_args = get_graph_args($target);
    do {
	    http_headers("text/html", $target->{config});
        print 'RRDs::graph(',
                join(",\n",
                $file, '-s', "-$back", @local_args,
                @{$target->{args}}, @graph_args, "VRULE:$oldsec#ff0000",
                "VRULE:$seconds#ff0000"),
                ')';
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

	http_headers("image/$imagetype", $target->{config});
		
	my $buf;
        # could be sendfile in Linux ;-)
        while(sysread PNG, $buf, 8192) {
                print $buf;
        }
	close PNG;
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
        push @{$target->{args}}, '-x', 'HOUR:1:HOUR:6:HOUR:2:0:%-H'
            if ($start_time-$end_time) <= 86400;
    } elsif( defined $start ) {
        my( $interval, $type ) = ($start =~ m/(\-\d+)([dwm])/);
                # regular -1d, -1m, -2w style start interval with no end
        if( defined $interval && defined $type ) {
                # start time is just interval-1
            $start_time = $interval-1 . $type;
                # end time is equal to interval
            $end_time = $interval . $type;
                # have to fix the x-axis for day interval
            push @{$target->{args}}, '-x', 'HOUR:1:HOUR:6:HOUR:2:0:%-H'
                if $type eq 'd';
        }
    }

    do {
        print_error('Undefined start or end time');
        return;
    } unless defined $start_time && defined $end_time;

    my @graph_args = get_graph_args($target);
        # unbuffered output
    $| = 1;
    http_headers("image/$imagetype", $target->{config});
    RRDs::graph('-',
            '-s', $start_time,
            '-e', $end_time,
            @{$target->{args}}, @graph_args);
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

	my $dir = $cfg->{workdir};
	$dir = $cfg->{logdir}
		if defined $cfg->{logdir};

	$target->{rrd} = $dir . '/' . $tdir . $name . '.rrd';

	$dir = $cfg->{workdir};
	$dir = $cfg->{imagedir}
		if defined $cfg->{imagedir};

    $target->{suppress} ||= '';

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
			my @stat = stat $file;
			if ($config_time < $stat[9]) {
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

		read_mrtg_config($cfgfile, $cfgref, \$order);
	}

    delete $targets{_};

	parse_directories();

	$config_time = time;
}

sub read_mrtg_config($$$)
{
	my ($file, $cfgref, $order) = @_;

	my @lines;

	open(CFG, "<$file") || print_error("Cannot open config file: $!");
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

	# FIXME: the sort is expensive
	for my $name (sort { $targets{$a}{order} <=> $targets{$b}{order} } keys %targets) {
		my $dir = $targets{$name}{directory}
			if defined $targets{$name}{directory};
		$dir = '' unless defined $dir;

		my $prefix = '';
		for my $component (split /\/+/, $dir) {
			unless (defined $directories{$prefix.$component}) {
				push (@{$directories{$prefix}{subdir}},
					$component);

				# For the directory, get the global parameters
				# from the # config of the first item of the
				# directory:
				$directories{$prefix}{config} =
					$targets{$name}{config};
				$directories{$prefix}{bodytag} =
					$targets{$name}{bodytag};
			}
			$prefix .= $component . '/';
		}
		unless (defined $directories{$dir}) {
			$directories{$dir}{config} =
				$targets{$name}{config};
			$directories{$dir}{bodytag} =
				$targets{$name}{bodytag};
		}

		push (@{$directories{$dir}{target}}, $name);
	}
}

sub print_dir($$) {
	my ($dir, $q) = @_;

	my $dir1 = $dir . '/';

	http_headers('text/html', $directories{$dir}{config});

	print <<EOT;
<HTML>
<HEAD>
<link type="text/css" rel="stylesheet" href="$directories{$dir}{config}{icondir}/style.css">
<TITLE>MRTG: Directory $dir1</TITLE>
</HEAD><BODY BGCOLOR=#ffffff>
EOT

	my $subdirs_printed;
    my( @graphs, @text );
	if (defined @{$directories{$dir}{subdir}}) {
		$subdirs_printed = 1;
		print <<EOT;
<small>All graphics are in PNG format. Make sure your browser supports
    PNG format. (Hint: Netscape 4 does NOT!!)</small>
<H1>MRTG subdirectories in the directory $dir1</H1>

<UL>
EOT
		for my $item (@{$directories{$dir}{subdir}}) {
			print "<LI><A HREF=\"$item/\">$item/</A>\n";
		}

		print "</UL>\n";
	}
	if (defined @{$directories{$dir}{target}}) {
		print "<HR>\n" if defined $subdirs_printed;
		print <<EOT;
<H1>MRTG graphs in the directory $dir1</H1>
<small>To get daily, weekly, monthly and yearly stats, click on a
graphic below to go a level deeper.</small>
EOT

		for my $item (@{$directories{$dir}{target}}) {
			my $itemname = $item;
            common_args($item, $targets{$item}, $q);
            my( undef, $xsize, $ysize )
                = do_image($targets{$item}, 'day', 0, 0);
			$itemname = $targets{$item}{title}
				if defined $targets{$item}{title};
                    # for each graph store its item and name in an
                    # anonymous hash and push onto the array @graphs
            push @graphs, {item => $item, name => $itemname};
            do {
                push @text, <<EOT;
<TR>
<TD><a name="$item">&nbsp;</a><a href="$item.html">$itemname</a><br>
Daily Graphic suppressed. More data is available
<a href="$item.html">here</a>.
</TR>
EOT
                next;
            } if $targets{$item}{suppress} =~ /d/;
			push @text, <<EOT;
<TR>
   <TD><a name="$item">&nbsp;</a><a href="$item.html">$itemname</a><br>
	<a href="$item.html"><img src="$item-day.$imagetype"
    width="$xsize" height="$ysize"
    border="0" align="top" vspace="10" alt="$item"></a><br clear="all">
   </TD>
</TR>
EOT
		} 
        print '<ul>', "\n";
        foreach my $graph( @graphs ) {
            print <<EOT;
<li><a href="#$graph->{item}">$graph->{name}</a>
EOT
        }
        print '</ul>', "\n";
        print '<TABLE BORDER=0 WIDTH=100%>', "\n";
        print @text;
		print "</TABLE>\n";
	}

    print <<EOT;
<h3><a href="/mrtg/special/">Issues/Problem events</a></h3>
<a href="http://www.rrdtool.org/"><img
    src="$directories{$dir}{config}{icondir}/rrdtool.gif" width="120"
    height="34" alt="RRDTool" border="0"></a>
</BODY>
</HTML>
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

sub dump_directories {
	print "Directories:\n";

	for my $dir (keys %directories) {
		print "Directory $dir:\n";
		for my $item (@{$directories{$dir}}) {
			print "\t$item\n";
		}
	}
}

sub print_error(@)
{
	print "Content-Type: text/plain\n\nError: ", join(' ', @_), "\n";
	exit 0;
}

#--BEGIN CGI--
#For CGI, use this:

use CGI;
my $q = new CGI;

handler($q);

#--END CGI--
#--BEGIN FCGI--
# For FastCGI, uncomment this and comment out the above:
#-# use FCGI;
#-# use CGI;
#-# 
#-# my $req = FCGI::Request();
#-# 
#-# while ($req->Accept >= 0) {
#-# 	my $q = new CGI;
#-# 	handler($q);
#-# }
#--END FCGI--

1;

