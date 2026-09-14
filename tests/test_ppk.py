import math
from pathlib import Path
import tempfile
import unittest
from datetime import datetime
from photo_report_app.ppk_core import (PPKError,Event,Solution,parse_mrk,normalize_nav,parse_pos,
                      interpolate,llh_xyz,xyz_llh,ned_xyz,p4_nominal_ned,camera_position,
                      match_photos,gps_datetime,gps_utc,Settings,choose_base,ICHI)


def sol(t,q=1,lat=28.0,lon=-106.0,h=1600):
    return Solution(t,lat,lon,h,q,15,0.01,0.01,0.02,7,4.5)


class Coordinates(unittest.TestCase):
    def test_ecef_roundtrip_both_hemispheres(self):
        for llh in [(28.3,-106.34,1600),(-35,179.99,-20),(0,0,0),(80,-70,7000)]:
            got=xyz_llh(*llh_xyz(*llh))
            self.assertAlmostEqual(got[0],llh[0],places=10)
            self.assertAlmostEqual(got[1],llh[1],places=10)
            self.assertAlmostEqual(got[2],llh[2],places=6)

    def test_down_positive_decreases_ellipsoidal_height(self):
        e=Event(1,2435,576627.659612,0,0,192)
        got=camera_position(sol(e.t),e,{},'mrk')
        self.assertAlmostEqual(got[2],1599.808,places=6)
        self.assertEqual(got[3],'MRK_NED')

    def test_nominal_camera_rotates_with_aircraft_yaw(self):
        def meta(y): return {f'XMP-drone-dji:Flight{k}Degree':v for k,v in [('Roll',0),('Pitch',0),('Yaw',y)]}
        self.assertEqual(p4_nominal_ned(meta(0)),(0.036,0.0,0.192))
        n,e,d=p4_nominal_ned(meta(90))
        self.assertAlmostEqual(n,0); self.assertAlmostEqual(e,.036); self.assertAlmostEqual(d,.192)

    def test_zero_mrk_is_not_valid_calibration(self):
        e=Event(1,2435,100,0,0,0)
        with self.assertRaises(PPKError): camera_position(sol(e.t),e,{},'mrk')
        got=camera_position(sol(e.t),e,{},'antenna')
        self.assertEqual(got[3],'ANTENA_SIN_COMPENSAR'); self.assertGreaterEqual(got[4],.2)

    def test_ichi_cannot_be_used_for_another_station(self):
        with self.assertRaises(PPKError): choose_base(Settings(base_mode='ichi'),{'marker':'OTHER','antenna':'','delta_hen':[0,0,0]})


class Interpolation(unittest.TestCase):
    def test_no_extrapolation_and_reject_gap(self):
        rows=[sol(100),sol(100.2)]
        for t in (99.99,100.21):
            with self.assertRaises(PPKError): interpolate(rows,[100,100.2],t)
        with self.assertRaises(PPKError): interpolate([sol(100),sol(101)],[100,101],100.5)

    def test_mixed_fix_float_is_float(self):
        s=interpolate([sol(100),sol(100.2,2)],[100,100.2],100.1)
        self.assertEqual(s.q,2)
        self.assertAlmostEqual(s.lat,28,places=9)

    def test_interpolation_crosses_antimeridian_in_ecef(self):
        a,b=sol(100,lon=179.999),sol(100.2,lon=-179.999)
        mid=interpolate([a,b],[100,100.2],100.1)
        self.assertGreater(abs(mid.lon),179.99)

    def test_duplicate_solution_epoch_keeps_lower_covariance(self):
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)/'x.pos'
            p.write_text('% GPST latitude(deg)\n2435 100 28 -106 1600 2 12 1 1 1 0 0 0 0 1\n2435 100 28 -106 1600 2 12 .1 .1 .2 0 0 0 0 1\n2435 100.2 28 -106 1600 2 12 .1 .1 .2 0 0 0 0 1\n')
            rows=parse_pos(p)
            self.assertEqual(len(rows),2); self.assertEqual(rows[0].sn,.1)


class TimingAndInput(unittest.TestCase):
    def test_gps_week_matches_flight_and_utc_leaps(self):
        ev=Event(1,2435,576627.659612,0,0,0)
        self.assertEqual(gps_datetime(ev.t).strftime('%Y-%m-%d %H:%M:%S'),'2026-09-12 16:10:27')
        self.assertEqual(gps_utc(ev.t).strftime('%Y-%m-%d %H:%M:%S'),'2026-09-12 16:10:09')
        self.assertEqual(gps_utc(0),datetime(1980,1,6))

    def test_mrk_duplicate_fails(self):
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)/'t.MRK'
            line='1\t576627.659612\t[2435]\t0,N\t0,E\t0,V\t0,Lat\n'
            p.write_text(line)
            self.assertEqual(parse_mrk(p)[0].index,1)
            p.write_text(line*2)
            with self.assertRaises(PPKError): parse_mrk(p)

    def test_missing_first_photo_does_not_shift_subsequent_images(self):
        events=[Event(1,2435,576627.1,0,0,0),Event(2,2435,576630.2,0,0,0)]
        photos=[Path('101_0006_0002.JPG'),Path('DJI_0007.JPG')]
        meta={photos[0].name:{'XMP-drone-dji:PhotoDiff':'SERIAL20260912161030'},photos[1].name:{'XMP-drone-dji:PhotoDiff':'SERIAL20260912161500'}}
        pairs,unused=match_photos(events,photos,meta,'101_0006_Timestamp.MRK',-6)
        self.assertIsNone(pairs[0][1]); self.assertEqual(pairs[1][1],photos[0]); self.assertEqual(unused,[photos[1]])

    def test_conflicting_filename_and_photodiff_fails(self):
        events=[Event(1,2435,576627.1,0,0,0)]
        p=Path('101_0006_0001.JPG')
        with self.assertRaises(PPKError): match_photos(events,[p],{p.name:{'XMP-drone-dji:PhotoDiff':'SERIAL20260912171027'}},'101_0006_Timestamp.MRK',-6)

    def test_galileo_header_fixed_without_changing_navigation_values(self):
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)/'x.26L'; dest=Path(td)/'fixed.rnx'
            head=f"{'     2.11':20s}{'E: GALILEO NAV DATA':40s}RINEX VERSION / TYPE\n"+' '*60+'END OF HEADER\n'
            body='E 6 2026 09 11 23 40 00  .123D-02\n'+('    1.234D+01\n'*7)
            p.write_text(head+body); before=p.read_bytes()
            self.assertTrue(normalize_nav(p,dest))
            got=dest.read_text().splitlines()
            self.assertEqual(float(got[0][:9]),3.03); self.assertEqual(got[2][:3],'E06')
            self.assertEqual(got[2][3:],body.splitlines()[0][3:]); self.assertEqual(p.read_bytes(),before)

    def test_malformed_galileo_is_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)/'x.26L'
            p.write_text(f"{'     2.11':20s}{'E: GALILEO NAV DATA':40s}RINEX VERSION / TYPE\n"+' '*60+'END OF HEADER\nBAD\n')
            with self.assertRaises(PPKError): normalize_nav(p,Path(td)/'out.rnx')


if __name__=='__main__': unittest.main()
